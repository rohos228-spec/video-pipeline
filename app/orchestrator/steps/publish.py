
from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.bots.publishers import ALL_PUBLISHERS, PublishGate, PublishResult, publish_everywhere
from app.models import Artifact, ArtifactKind, Project, ProjectStatus
from app.services import publications_store as pstore
from app.services import publish_accounts
from app.services.notification_log import notify
from app.settings import settings
from app.telegram import publish_texts as ptexts

_META_KEY = "published_platforms"
_UNCONFIRMED_KEY = "publish_unconfirmed"
_SKIPPED_KEY = "publish_skipped"
_HUMAN_KEY = "publish_needs_human"
_AWAITING_KEY = "publish_awaiting_approval"
_CLOSED_KEY = "publish_closed_by_human"
_DEFERRED_KEY = "publish_deferred"
_MARKER_KEYS = (_SKIPPED_KEY, _HUMAN_KEY, _AWAITING_KEY, _CLOSED_KEY, _DEFERRED_KEY)

CAPTION_LIMIT = 2200


def caption_for(project: Project) -> str:
    return (project.topic or project.slug)[:CAPTION_LIMIT]


def caption_sha256(caption: str) -> str:
    return hashlib.sha256(caption.encode("utf-8")).hexdigest()


async def preview_publication(session: AsyncSession, project: Project) -> dict:
    from app.services.artifact_verifier import verify_artifact

    already = (project.meta or {}).get(_META_KEY, {})
    already = already if isinstance(already, dict) else {}
    result = {"dry_run":True, "send_allowed":False, "requires_approval":True,
              "project_status":project.status.value, "artifact_uuid":None,
              "caption_characters":len((project.topic or project.slug)[:2200]),
              "platforms":[{"platform":cls.platform, "already_recorded":cls.platform in already}
                           for cls in ALL_PUBLISHERS]}
    with session.no_autoflush:
        final = await session.scalar(select(Artifact).where(
            Artifact.project_id==project.id, Artifact.kind==ArtifactKind.final_video
        ).order_by(Artifact.id.desc()).limit(1))
    if final is None:
        result["validation"] = {"status":"failed", "reason":"final_artifact_missing"}
        return result
    try:
        result["validation"] = await verify_artifact(final.path, root=project.data_dir, kind="assembly")
    except ValueError:
        result["validation"] = {"status":"failed", "reason":"artifact_outside_project"}
        return result
    except OSError:
        result["validation"] = {"status":"unknown", "reason":"artifact_unreadable"}
        return result
    result["artifact_uuid"] = final.uuid
    return result


class _StoreGate(PublishGate):

    def __init__(self, session: AsyncSession, project_id: int, sha256: str, artifact_uuid: str | None,
                 caption_sha: str = "") -> None:
        self.session, self.project_id, self.sha256, self.artifact_uuid = session, project_id, sha256, artifact_uuid
        self.caption_sha = caption_sha
        self.owner = pstore.new_owner()
        self.pub_ids: dict[str, int] = {}
        self.recorded: set[str] = set()
        self.not_claimed: dict[str, str] = {}
        self.deferred: dict[str, dict] = {}

    async def recent_clicks(self, platform: str) -> list[float] | None:
        return await publish_accounts.recent_click_times(self.session, platform)

    async def claim(self, platform: str) -> bool:
        c = await pstore.claim(self.session, project_id=self.project_id, platform=platform,
                               sha256=self.sha256, artifact_uuid=self.artifact_uuid, owner=self.owner)
        if c.outcome == pstore.ACQUIRED:
            self.pub_ids[platform] = c.pub_id
            return True
        self.not_claimed[platform] = c.outcome
        return False

    async def before_click(self, platform: str) -> bool:
        pid = self.pub_ids.get(platform)
        if pid is None:
            return False
        if not await pstore.consume_approval(self.session, project_id=self.project_id, platform=platform,
                                             sha256=self.sha256, caption_sha256=self.caption_sha):
            self.not_claimed[platform] = "no_human_approval"
            return False
        return await pstore.mark_clicked(self.session, pid, self.owner)

    async def record(self, result: PublishResult) -> None:
        pid = self.pub_ids.get(result.platform)
        status = result.status if result.status in pstore.ALL_STATUSES else (
            pstore.S_CONFIRMED if result.ok else pstore.S_FAILED)
        code = result.error_code or ((result.error or "")[:200] or None)
        if status == pstore.S_SKIPPED and result.retry_after:
            self.deferred[result.platform] = {"retry_after": float(result.retry_after), "code": code}
        logger.info("[#{}] publish record {}: status={} code={} account={} screenshot={}", self.project_id,
                    result.platform, status, code, result.account_label or "—", result.screenshot or "—")
        if pid is not None:
            await pstore.record_result(self.session, pid, self.owner, status=status, post_url=result.url,
                                       post_id=result.post_id, error_code=code, screenshot=result.screenshot,
                                       account_id=result.account_label)
        else:
            await pstore.record_unclaimed(
                self.session, project_id=self.project_id, platform=result.platform, sha256=self.sha256,
                artifact_uuid=self.artifact_uuid,
                status=status,
                clicked=result.clicked or status in (pstore.S_CONFIRMED, pstore.S_UNCONFIRMED),
                post_url=result.url, post_id=result.post_id, error_code=code, screenshot=result.screenshot,
                account_id=result.account_label)
        self.recorded.add(result.platform)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


async def _latest_final(session: AsyncSession, project: Project) -> Artifact | None:
    return (await session.execute(
        select(Artifact).where(Artifact.project_id == project.id, Artifact.kind == ArtifactKind.final_video)
        .order_by(Artifact.id.desc()).limit(1))).scalar_one_or_none()


async def _file_sha(path: str) -> str:
    try:
        return await pstore.sha256_of_file_async(path)
    except OSError:
        return "no-file"


_SHA_CACHE: dict[tuple[str, int, int], str] = {}


async def _file_sha_cached(path: str) -> str:
    try:
        st = os.stat(path)
    except OSError:
        return "no-file"
    key = (path, st.st_size, st.st_mtime_ns)
    hit = _SHA_CACHE.get(key)
    if hit is None:
        hit = await _file_sha(path)
        if hit != "no-file":
            if len(_SHA_CACHE) > 64:
                _SHA_CACHE.clear()
            _SHA_CACHE[key] = hit
    return hit


def _sync_meta(project: Project, rows: list[dict], platforms: list[str]) -> dict:
    summ = pstore.summarize(rows, platforms)
    by = {r["platform"]: r for r in rows}
    meta = dict(project.meta or {})
    already = dict(meta[_META_KEY]) if isinstance(meta.get(_META_KEY), dict) else {}
    unconfirmed = dict(meta[_UNCONFIRMED_KEY]) if isinstance(meta.get(_UNCONFIRMED_KEY), dict) else {}
    for p in summ["confirmed"]:
        already[p] = by[p]["post_url"] or by[p]["post_id"] or ""
        unconfirmed.pop(p, None)
    for p in platforms:
        r = by.get(p)
        if r and r["status"] in (pstore.S_UNCONFIRMED, pstore.S_CLICKED) and p not in already:
            unconfirmed[p] = r["post_url"] or ""
        elif r and p in unconfirmed and r["status"] not in (pstore.S_UNCONFIRMED, pstore.S_CLICKED):
            unconfirmed.pop(p, None)
    meta[_META_KEY], meta[_UNCONFIRMED_KEY] = already, unconfirmed
    project.meta = meta
    flag_modified(project, "meta")
    return summ


def _set_markers(project: Project, **markers: dict | None) -> None:
    meta = dict(project.meta or {})
    for key, value in markers.items():
        if value:
            meta[key] = value
        else:
            meta.pop(key, None)
    project.meta = meta
    flag_modified(project, "meta")


async def _skip_publication(session: AsyncSession, project: Project) -> None:
    final = await _latest_final(session, project)
    sha = await _file_sha(final.path) if final is not None else "no-artifact"
    reason = "SOCIAL_PUBLISH_ENABLED=false"
    for cls in ALL_PUBLISHERS:
        await pstore.mark_skipped(session, project_id=project.id, platform=cls.platform, sha256=sha,
                                  artifact_uuid=final.uuid if final is not None else None, reason=reason)
    meta = dict(project.meta or {})
    meta[_SKIPPED_KEY] = {"reason": reason, "at": _now_iso(), "sha256": sha,
                          "artifact_uuid": final.uuid if final is not None else None,
                          "text": "Публикация пропущена: автопубликация выключена, ничего не опубликовано."}
    project.meta = meta
    flag_modified(project, "meta")
    project.status = ProjectStatus.assembled
    await session.flush()
    logger.info("[#{}] publish_skipped (SOCIAL_PUBLISH_ENABLED=false): опубликовано 0 платформ", project.id)


def is_publish_skipped(project: Project) -> bool:
    meta = project.meta or {}
    return bool(meta.get(_SKIPPED_KEY)) and project.status is not ProjectStatus.published


async def publish_hold_reason(session: AsyncSession, project: Project) -> str | None:
    meta = project.meta or {}
    if not any(isinstance(meta.get(k), dict) and meta.get(k) for k in _MARKER_KEYS):
        return None
    final = await _latest_final(session, project)
    current = final.uuid if final is not None else None
    for key in _MARKER_KEYS:
        marker = meta.get(key)
        if not isinstance(marker, dict) or not marker:
            continue
        if key == _SKIPPED_KEY and settings.social_publish_enabled:
            continue
        if key == _DEFERRED_KEY and not _active_deferred(marker):
            continue
        marked = marker.get("artifact_uuid")
        if marked and current and marked != current:
            continue
        return key
    return None


def _active_deferred(marker: object, now: float | None = None) -> dict[str, dict]:
    if not isinstance(marker, dict):
        return {}
    now = time.time() if now is None else now
    plats = marker.get("platforms")
    if not isinstance(plats, dict):
        return {}
    return {p: v for p, v in plats.items() if isinstance(v, dict) and float(v.get("retry_after") or 0) > now}


def _legacy_map(meta: dict, rows: list[dict] | None = None) -> dict[str, bool]:
    out: dict[str, bool] = {}
    unconfirmed = meta.get(_UNCONFIRMED_KEY)
    if isinstance(unconfirmed, dict):
        out.update({p: False for p in unconfirmed})
    published = meta.get(_META_KEY)
    if isinstance(published, dict):
        out.update({p: bool(v) for p, v in published.items()})
    have_rows = {r["platform"] for r in rows or ()}
    return {p: v for p, v in out.items() if p not in have_rows}


async def apply_project_state(session: AsyncSession, project: Project, *, rows: list[dict],
                              platforms: list[str], sha: str, artifact_uuid: str | None,
                              approved: set[str]) -> dict:
    legacy = _legacy_map(project.meta or {}, rows)
    st = pstore.derive_project_state(rows, platforms, approved, legacy=legacy)
    state = st["state"]
    base = {"at": _now_iso(), "sha256": sha, "artifact_uuid": artifact_uuid}
    if state == pstore.PS_PUBLISHED:
        _set_markers(project, **{k: None for k in _MARKER_KEYS})
        project.status = ProjectStatus.published
        logger.info("[#{}] publish done, все {} платформ подтверждены", project.id, len(platforms))
    elif state == pstore.PS_WORKING:
        _set_markers(project, **{_HUMAN_KEY: None, _AWAITING_KEY: None, _CLOSED_KEY: None})
        logger.warning("[#{}] {}/{} подтверждено; к повтору {}, заняты {}", project.id, len(st["confirmed"]),
                       len(platforms), st["retry_approved"], st["busy"])
    elif state == pstore.PS_NEEDS_HUMAN:
        _set_markers(project, **{
            _HUMAN_KEY: {**base, "platforms": st["needs_human"], "confirmed": st["confirmed"],
                         "text": "Автоповтор запрещён: проверьте площадки вручную и подтвердите в панели «Публикации»."},
            _AWAITING_KEY: None, _CLOSED_KEY: None, _SKIPPED_KEY: None})
        project.status = ProjectStatus.assembled
        logger.warning("[#{}] publish: ждёт человека {}, статус -> assembled", project.id, st["needs_human"])
    elif state == pstore.PS_AWAITING_APPROVAL:
        _set_markers(project, **{
            _AWAITING_KEY: {**base, "platforms": st["awaiting_approval"], "confirmed": st["confirmed"],
                            "text": "Публикация не начата: нужно подтверждение человека на каждую площадку."},
            _HUMAN_KEY: None, _CLOSED_KEY: None, _SKIPPED_KEY: None})
        project.status = ProjectStatus.assembled
    else:
        _set_markers(project, **{
            _CLOSED_KEY: {**base, "platforms": st["manual_failed"], "confirmed": st["confirmed"],
                          "text": "Человек закрыл оставшиеся площадки как «не опубликовано»."},
            _HUMAN_KEY: None, _AWAITING_KEY: None, _SKIPPED_KEY: None})
        project.status = ProjectStatus.assembled
    await session.flush()
    return st


async def publication_facts(session: AsyncSession, project: Project) -> dict:
    platforms = [cls.platform for cls in ALL_PUBLISHERS]
    final = await _latest_final(session, project)
    caption = caption_for(project)
    csha = caption_sha256(caption)
    facts: dict = {
        "project_id": project.id, "project_status": project.status.value,
        "publish_enabled": bool(settings.social_publish_enabled), "requires_human_approval": True,
        "caption": caption, "caption_sha256": csha, "caption_characters": len(caption), "schedule": None,
        "video": None, "sha256": None, "markers": {k: (project.meta or {}).get(k) for k in _MARKER_KEYS
                                                   if (project.meta or {}).get(k)},
    }
    sha = None
    if final is not None:
        sha = await _file_sha_cached(final.path)
        facts["sha256"] = sha
        facts["video"] = {"artifact_uuid": final.uuid,
                          "url": f"/api/artifacts/{final.uuid}/file?project_id={project.id}"}
    rows = await pstore.list_for(session, project.id, sha) if sha else []
    approvals = await pstore.list_approvals(session, project.id, sha, csha) if sha else {}
    actions = await pstore.list_manual_actions(session, project.id)
    last_action = {a["platform"]: a for a in actions if a["sha256"] == sha}
    by = {r["platform"]: r for r in rows}
    approved = {p for p, a in approvals.items() if a["valid"]}
    items = []
    for p in platforms:
        r = by.get(p)
        state = pstore.display_state(r) if r else "not_started"
        busy = bool(r and r["status"] == pstore.S_PLANNED and not r.get("clicked_at")
                    and (r.get("lease_until") or 0) >= time.time())
        confirmed = state == pstore.D_CONFIRMED
        items.append({
            "platform": p, "state": state,
            "state_label": pstore.DISPLAY_LABELS_RU.get(state, "ещё не начиналось"),
            "status": r["status"] if r else None,
            "post_url": r["post_url"] if r else None, "post_id": r["post_id"] if r else None,
            "error_code": r["error_code"] if r else None, "attempts": r["attempts"] if r else 0,
            "clicked": bool(r and r.get("clicked_at")), "account_id": r["account_id"] if r else None,
            "approved": p in approved,
            "approved_by": approvals[p]["approved_by"] if p in approvals and approvals[p]["valid"] else None,
            "manual": ({k: last_action[p][k] for k in ("action", "actor", "reason", "post_url", "created_at")}
                       if p in last_action else None),
            "can_confirm_manual": bool(r) and not confirmed and not busy,
            "can_mark_failed": bool(r) and not confirmed and not busy,
            "can_approve": bool(sha) and not confirmed and not busy
                           and (r is None or pstore.is_auto_retryable(r, time.time())
                                or r["status"] == pstore.S_PLANNED and not r.get("clicked_at")),
        })
    facts["platforms"] = items
    facts["derived"] = (pstore.derive_project_state(rows, platforms, approved, legacy=_legacy_map(project.meta or {}, rows),
                                                    publish_enabled=bool(settings.social_publish_enabled))
                        if sha else None)
    return facts


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    from app.services.actions_v1 import get_permission
    from app.services.observation import _attempt
    from app.services.media_jobs import MediaPolicyError
    ctx = _attempt.get()
    if get_permission(project.id) is not None or (ctx and ctx.scope.mode == "managed"):
        raise MediaPolicyError("managed publication is disabled; use read-only preview")
    if project.status is not ProjectStatus.publishing:
        return
    if not settings.social_publish_enabled:
        await _skip_publication(session, project)
        return

    final = await _latest_final(session, project)
    if final is None:
        raise RuntimeError("нет финального видео для публикации")

    caption = caption_for(project)
    if not caption.strip():
        raise RuntimeError("подпись пуста: публикация не начата")
    sha = await _file_sha(final.path)
    if sha == "no-file":
        raise RuntimeError("файл финального видео не читается: публикация не начата")
    csha = caption_sha256(caption)
    platforms = [cls.platform for cls in ALL_PUBLISHERS]

    meta = project.meta or {}
    legacy: set[str] = set()
    for key in (_META_KEY, _UNCONFIRMED_KEY):
        if isinstance(meta.get(key), dict):
            legacy |= set(meta[key].keys())

    rows = await pstore.list_for(session, project.id, sha)
    legacy -= {r["platform"] for r in rows}
    summ = pstore.summarize(rows, platforms)
    approvals = await pstore.list_approvals(session, project.id, sha, csha)
    approved = {p for p, a in approvals.items() if a["valid"]}
    old_marker = meta.get(_DEFERRED_KEY) if isinstance(meta.get(_DEFERRED_KEY), dict) else {}
    if old_marker.get("artifact_uuid") not in (None, final.uuid):
        old_marker = {}
    deferred: dict[str, dict] = dict(_active_deferred(old_marker))
    unapproved = set(platforms) - approved
    skip_platforms = (set(legacy) | set(summ["confirmed"]) | set(summ["needs_human"])
                      | set(summ["manual_failed"]) | unapproved | set(deferred))
    logger.info("[#{}] publishing {} (skip={}, approved={})", project.id, final.path,
                sorted(skip_platforms) or "—", sorted(approved) or "—")

    results: list[PublishResult] = []
    gate = _StoreGate(session, project.id, sha, final.uuid, csha)
    if not all(p in skip_platforms for p in platforms):
        results = await publish_everywhere(Path(final.path), caption, skip_platforms=skip_platforms, gate=gate,
                                           project_label=project.id)
        for r in results:
            if r.platform not in gate.recorded:
                await gate.record(r)

    rows = await pstore.list_for(session, project.id, sha)
    _sync_meta(project, rows, platforms)
    approvals = await pstore.list_approvals(session, project.id, sha, csha)
    approved = {p for p, a in approvals.items() if a["valid"]}
    prev_awaiting = (project.meta or {}).get(_AWAITING_KEY) or {}
    new_deferred = {p: v for p, v in gate.deferred.items() if p not in deferred}
    deferred.update(gate.deferred)
    by_status = {r["platform"]: r["status"] for r in rows}
    deferred = {p: v for p, v in deferred.items() if by_status.get(p) == pstore.S_SKIPPED}
    legacy_now = _legacy_map(project.meta or {}, rows)
    preview = pstore.derive_project_state(rows, platforms, approved, legacy=legacy_now)
    pure_wait = (bool(deferred) and preview["state"] == pstore.PS_WORKING and not preview["busy"]
                 and set(preview["retry_approved"]) <= set(deferred))
    if pure_wait and not preview["needs_human"]:
        _set_markers(project, **{
            _DEFERRED_KEY: {"at": _now_iso(), "sha256": sha, "artifact_uuid": final.uuid, "platforms": deferred,
                            "text": "Публикация перенесена (лимит или окно публикации); повтор после указанного времени."},
            _HUMAN_KEY: None, _AWAITING_KEY: None, _CLOSED_KEY: None, _SKIPPED_KEY: None})
        project.status = ProjectStatus.assembled
        await session.flush()
        st = {**preview, "state": "deferred"}
        logger.info("[#{}] publish: перенесено {} до {}", project.id, sorted(deferred),
                    {p: v["retry_after"] for p, v in deferred.items()})
    else:
        st = await apply_project_state(session, project, rows=rows, platforms=platforms, sha=sha,
                                       artifact_uuid=final.uuid,
                                       approved=approved - set(deferred) if pure_wait else approved)
        if st["state"] != pstore.PS_PUBLISHED:
            _set_markers(project, **{_DEFERRED_KEY: (
                {"at": _now_iso(), "sha256": sha, "artifact_uuid": final.uuid, "platforms": deferred,
                 "text": "Часть площадок перенесена (лимит или окно публикации)."} if deferred else None)})

    if st["state"] == pstore.PS_AWAITING_APPROVAL and st["awaiting_approval"] and settings.telegram_active:
        try:
            from app.services.hitl import request_publish_approval
            await request_publish_approval(bot, session, project, platforms=list(st["awaiting_approval"]),
                                           sha256=sha, caption=caption, caption_sha256=csha,
                                           artifact_uuid=final.uuid)
        except Exception:
            logger.exception("[#{}] publish: запрос approve_publish в Telegram не создан", project.id)

    shown = [r for r in results if r.status != pstore.S_SKIPPED]
    human = [r for r in shown if r.needs_human and not r.clicked]
    parts: list[str] = []
    if shown or gate.not_claimed:
        parts.append(ptexts.publish_summary_text(project.id, shown, skipped=skip_platforms,
                                                 not_claimed=gate.not_claimed))
    elif st["state"] == pstore.PS_AWAITING_APPROVAL and prev_awaiting.get("artifact_uuid") != final.uuid:
        parts.append(ptexts.awaiting_approval_text(project.id, st["awaiting_approval"], sha))
    if human:
        parts.append("Нужен человек:\n" + "\n".join(
            f"- {r.platform}: {publish_accounts.refusal_text(r.error_code)}" for r in human))
    if new_deferred:
        parts.append("Перенесено, повтор позже (без вашего участия):\n" + "\n".join(
            f"- {p}: {publish_accounts.refusal_text(v.get('code'))} Не раньше "
            f"{datetime.fromtimestamp(v['retry_after'], timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}."
            for p, v in sorted(new_deferred.items())))
    if parts and st["state"] == pstore.PS_NEEDS_HUMAN and shown:
        parts.append(ptexts.needs_human_text(project.id, st["needs_human"]))
    if parts:
        try:
            await notify(bot, "send_message", settings.telegram_owner_chat_id, "\n\n".join(parts),
                         kind="publish_summary", project_id=project.id)
        except Exception:
            logger.exception("[#{}] publish: Telegram не отправлен", project.id)
    await session.flush()
