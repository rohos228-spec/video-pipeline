"""Шаг 12: публикация готового ролика на 5 площадок через MoreLogin-профиль.

Безопасность (A13/A14): намерение пишется в журнал `publications` ДО клика, итог каждой платформы
сразу после неё, автоповтор только при сбое ДО клика, `published` только при confirmed с URL/ID
на всех платформах реестра. При SOCIAL_PUBLISH_ENABLED=false ничего не публикуется, проект
помечается «публикация пропущена» (meta.publish_skipped), а не published.
Живой платформой не проверялось.
"""

from __future__ import annotations

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
from app.services.notification_log import notify
from app.settings import settings

_META_KEY = "published_platforms"
_UNCONFIRMED_KEY = "publish_unconfirmed"
_SKIPPED_KEY = "publish_skipped"
_HUMAN_KEY = "publish_needs_human"


async def preview_publication(session: AsyncSession, project: Project) -> dict:
    """Read-only validation of the existing publish job. Never opens a browser."""
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
    """Мост publishers -> publications_store. Каждый шаг журнала фиксируется commit-ом сразу."""

    def __init__(self, session: AsyncSession, project_id: int, sha256: str, artifact_uuid: str | None) -> None:
        self.session, self.project_id, self.sha256, self.artifact_uuid = session, project_id, sha256, artifact_uuid
        self.owner = pstore.new_owner()
        self.pub_ids: dict[str, int] = {}
        self.recorded: set[str] = set()
        self.not_claimed: dict[str, str] = {}

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
        return pid is not None and await pstore.mark_clicked(self.session, pid, self.owner)

    async def record(self, result: PublishResult) -> None:
        pid = self.pub_ids.get(result.platform)
        status = result.status if result.status in pstore.ALL_STATUSES else (
            pstore.S_CONFIRMED if result.ok else pstore.S_FAILED)
        code = result.error_code or ((result.error or "")[:200] or None)
        if pid is not None:
            await pstore.record_result(self.session, pid, self.owner, status=status, post_url=result.url,
                                       post_id=result.post_id, error_code=code)
        else:
            await pstore.record_unclaimed(
                self.session, project_id=self.project_id, platform=result.platform, sha256=self.sha256,
                artifact_uuid=self.artifact_uuid,
                status=status,
                clicked=result.clicked or status in (pstore.S_CONFIRMED, pstore.S_UNCONFIRMED),
                post_url=result.url, post_id=result.post_id, error_code=code)
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


def _sync_meta(project: Project, rows: list[dict], platforms: list[str]) -> dict:
    """Зеркало журнала в project.meta (Studio и обратная совместимость).

    published_platforms содержит только confirmed с URL/ID."""
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
    meta[_META_KEY], meta[_UNCONFIRMED_KEY] = already, unconfirmed
    project.meta = meta
    flag_modified(project, "meta")
    return summ


async def _skip_publication(session: AsyncSession, project: Project) -> None:
    """SOCIAL_PUBLISH_ENABLED=false: ничего не публикуем, `published` НЕ ставим."""
    final = await _latest_final(session, project)
    sha = await _file_sha(final.path) if final is not None else "no-artifact"
    reason = "SOCIAL_PUBLISH_ENABLED=false"
    for cls in ALL_PUBLISHERS:
        await pstore.mark_skipped(session, project_id=project.id, platform=cls.platform, sha256=sha,
                                  artifact_uuid=final.uuid if final is not None else None, reason=reason)
    meta = dict(project.meta or {})
    meta[_SKIPPED_KEY] = {"reason": reason, "at": _now_iso(), "sha256": sha,
                          "text": "Публикация пропущена: автопубликация выключена, ничего не опубликовано."}
    project.meta = meta
    flag_modified(project, "meta")
    # published был бы ложным, publishing залипает: assembled = «собрано, не опубликовано».
    project.status = ProjectStatus.assembled
    await session.flush()
    logger.info("[#{}] publish_skipped (SOCIAL_PUBLISH_ENABLED=false): опубликовано 0 платформ", project.id)


def is_publish_skipped(project: Project) -> bool:
    """Проект помечен «публикация пропущена» и не опубликован (метрики батчей, Studio)."""
    meta = project.meta or {}
    return bool(meta.get(_SKIPPED_KEY)) and project.status is not ProjectStatus.published


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

    caption = (project.topic or project.slug)[:2200]
    if not caption.strip():
        raise RuntimeError("подпись пуста: публикация не начата")
    sha = await _file_sha(final.path)
    if sha == "no-file":
        raise RuntimeError("файл финального видео не читается: публикация не начата")
    platforms = [cls.platform for cls in ALL_PUBLISHERS]

    # Записи meta до появления журнала считаем «итог уже есть»: автоповтор запрещён.
    meta = project.meta or {}
    legacy: set[str] = set()
    for key in (_META_KEY, _UNCONFIRMED_KEY):
        if isinstance(meta.get(key), dict):
            legacy |= set(meta[key].keys())

    rows = await pstore.list_for(session, project.id, sha)
    summ = pstore.summarize(rows, platforms)
    skip_platforms = set(legacy) | set(summ["confirmed"]) | set(summ["needs_human"])
    logger.info("[#{}] publishing {} (skip={})", project.id, final.path, sorted(skip_platforms) or "—")

    results: list[PublishResult] = []
    gate = _StoreGate(session, project.id, sha, final.uuid)
    if not all(p in skip_platforms for p in platforms):
        results = await publish_everywhere(Path(final.path), caption, skip_platforms=skip_platforms, gate=gate)
        for r in results:  # вызов без gate (подмена в тестах): итог всё равно попадает в журнал
            if r.platform not in gate.recorded:
                await gate.record(r)

    rows = await pstore.list_for(session, project.id, sha)
    summ = _sync_meta(project, rows, platforms)
    # Платформы из старого meta без строки журнала: это не «к повтору», а итог, ждущий человека.
    legacy_pub = (project.meta or {}).get(_META_KEY) or {}
    for p in [x for x in summ["retryable"] if x in legacy]:
        summ["retryable"].remove(p)
        if legacy_pub.get(p):
            summ["confirmed"].append(p)
        else:
            summ["needs_human"].append(p)
    summ["all_confirmed"] = len(set(summ["confirmed"])) == len(platforms)

    lines: list[str] = [f"Публикация проекта #{project.id}:"]
    for r in results:
        if r.ok:
            lines.append(f"- {r.platform}: OK {r.url or ''}")
        elif r.status == "unconfirmed":
            lines.append(f"- {r.platform}: НЕ ПОДТВЕРЖДЕНО — проверьте вручную {r.url or ''}")
        elif r.clicked:
            lines.append(f"- {r.platform}: СБОЙ ПОСЛЕ КЛИКА — проверьте вручную, автоповтора не будет: {r.error or ''}")
        else:
            lines.append(f"- {r.platform}: FAIL до клика — {r.error or ''}")
    if skip_platforms:
        lines.append(f"(пропущены, итог уже есть: {', '.join(sorted(skip_platforms))})")
    if gate.not_claimed:
        lines.append("(заняты другим запуском или ждут человека: "
                     + ", ".join(f"{k}={v}" for k, v in sorted(gate.not_claimed.items())) + ")")
    if results or gate.not_claimed:
        try:
            await notify(bot, "send_message", settings.telegram_owner_chat_id, "\n".join(lines),
                         kind="publish_summary", project_id=project.id)
        except Exception:  # noqa: BLE001 — итог уже в журнале, уведомление вторично
            logger.exception("[#{}] publish: Telegram не отправлен", project.id)

    if summ["all_confirmed"]:
        project.status = ProjectStatus.published
        logger.info("[#{}] publish done, все {} платформ подтверждены", project.id, len(platforms))
    elif summ["retryable"] or summ["busy"]:
        # Есть платформы, где клика точно не было: воркер повторит позже, подтверждённые пропустит.
        logger.warning("[#{}] {}/{} подтверждено; к повтору {}, заняты {}", project.id,
                       len(summ["confirmed"]), len(platforms), summ["retryable"], summ["busy"])
    else:
        # Повторять нечего: остальное ждёт человека. В publishing не залипаем.
        meta = dict(project.meta or {})
        meta[_HUMAN_KEY] = {"platforms": summ["needs_human"], "confirmed": summ["confirmed"], "at": _now_iso(),
                            "text": "Автоповтор запрещён: проверьте площадки вручную."}
        project.meta = meta
        flag_modified(project, "meta")
        project.status = ProjectStatus.assembled
        logger.warning("[#{}] publish: ждёт человека {}, статус -> assembled", project.id, summ["needs_human"])
    await session.flush()
