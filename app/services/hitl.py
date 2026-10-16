"""HITL-гейты: создаём запрос на подтверждение в Telegram, шлём артефакт с
инлайн-кнопками и ждём решения пользователя (polling по БД).
"""

from __future__ import annotations

import asyncio
import html as _html
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import HITLDecision, HITLKind, HITLRequest, Project
from app.services.event_bus import publish_hitl_event
from app.services.notification_log import notify
from app.settings import settings


def _keyboard(
    hitl_id: int,
    *,
    allow_edit: bool = False,
    allow_original: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="✅ Одобрить", callback_data=f"hitl:{hitl_id}:approve"),
            InlineKeyboardButton(text="🔁 Перегенерировать", callback_data=f"hitl:{hitl_id}:regen"),
        ],
    ]
    if allow_edit:
        rows.append([
            InlineKeyboardButton(
                text="✏️ Изменить промт",
                callback_data=f"hitl:{hitl_id}:edit",
            ),
        ])
    # Вторая строка второго ряда: «Оригинал» (без сжатия TG) + «Отклонить».
    last_row = []
    if allow_original:
        last_row.append(
            InlineKeyboardButton(
                text="📎 Скачать оригинал",
                callback_data=f"hitl:{hitl_id}:original",
            )
        )
    last_row.append(
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"hitl:{hitl_id}:reject"),
    )
    rows.append(last_row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _split_html(body: str, limit: int) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(body):
        end = min(i + limit, len(body))
        if end < len(body):
            amp = body.rfind("&", i, end)
            if amp != -1 and body.find(";", amp, end) == -1 and end - amp <= 8:
                end = amp
        out.append(body[i:end])
        i = end
    return out or [body]


async def create_hitl(
    session: AsyncSession,
    project: Project,
    kind: HITLKind,
    payload: dict | None = None,
    frame_id: int | None = None,
) -> HITLRequest:
    req = HITLRequest(
        project_id=project.id,
        frame_id=frame_id,
        kind=kind,
        payload=payload or {},
    )
    session.add(req)
    await session.flush()
    from app.services.observation import _attempt, emit
    ctx = _attempt.get()
    if ctx and ctx.scope.project_id == project.id and kind in {HITLKind.approve_plan, HITLKind.approve_script}:
        emit('human_approval_pending', {'hitl_request_id': req.id, 'kind': kind.value,
            'reason': 'подтверждение плана человеком' if kind == HITLKind.approve_plan else 'подтверждение сценария человеком',
            'initiator': 'original executor', 'transactional': True})
    return req


async def send_hitl_text(
    bot: Bot | None,
    session: AsyncSession,
    project: Project,
    kind: HITLKind,
    title: str,
    text: str,
    payload: dict | None = None,
    frame_id: int | None = None,
    keyboard=None,
) -> HITLRequest:
    p = dict(payload or {})
    if "text" not in p and text:
        p["text"] = text
    req = await create_hitl(session, project, kind, payload=p, frame_id=frame_id)
    if not settings.telegram_active:
        await publish_hitl_event(
            project.id,
            req.id,
            event_type="hitl_pending",
            payload={
                "kind": kind.value,
                "title": title,
                "text": text[:500],
                "frame_id": frame_id,
            },
        )
        return req
    if bot is None:
        raise RuntimeError("telegram_active but bot is None")
    # Используем HTML parse_mode и экранируем произвольный текст — так Telegram
    # не спотыкается о «грязный» markdown из LLM-ответа (звёздочки, скобки,
    # бэктики в непредвидимых местах).
    body = f"<b>{_html.escape(title)}</b>\n\n{_html.escape(text)}"
    chunks = _split_html(body, 3800)

    # были снизу. Иначе при дроблении на куски кнопки прилипают к первой
    # части, и ниже идёт «голый» хвост текста.
    msg = None
    for i, c in enumerate(chunks):
        is_last = i == len(chunks) - 1
        msg = await notify(
            bot, "send_message",
            settings.telegram_owner_chat_id,
            c,
            kind="hitl_request",
            project_id=project.id,
            parse_mode="HTML",
            reply_markup=(keyboard or _keyboard)(req.id) if is_last else None,
        )
    assert msg is not None
    req.tg_message_id = msg.message_id
    return req


async def send_hitl_photo(
    bot: Bot | None,
    session: AsyncSession,
    project: Project,
    kind: HITLKind,
    photo_path: str,
    caption: str,
    payload: dict | None = None,
    frame_id: int | None = None,
    allow_edit: bool = False,
) -> HITLRequest:
    """Шлёт картинку как фото; если файл > 10 MB (Telegram-лимит для photo) —
    отправляет как документ (лимит 50 MB). Подпись и кнопки одинаковые."""
    import os as _os

    from aiogram.exceptions import TelegramBadRequest
    from aiogram.types import FSInputFile

    PHOTO_LIMIT = 9 * 1024 * 1024  # с запасом до 10 MB
    CAPTION_LIMIT = 1000

    # Сохраняем путь к оригиналу в payload — для кнопки «📎 Скачать оригинал»,
    # чтобы бот потом смог прислать файл через send_document без сжатия TG.
    payload = dict(payload or {})
    payload.setdefault("photo_path", photo_path)
    req = await create_hitl(session, project, kind, payload=payload, frame_id=frame_id)
    if not settings.telegram_active:
        await publish_hitl_event(
            project.id,
            req.id,
            event_type="hitl_pending",
            payload={
                "kind": kind.value,
                "photo_path": photo_path,
                "caption": caption[:500],
                "frame_id": frame_id,
            },
        )
        return req
    if bot is None:
        raise RuntimeError("telegram_active but bot is None")
    kb = _keyboard(req.id, allow_edit=allow_edit, allow_original=True)

    # Если caption длиннее лимита TG — шлём фото без кнопок, потом текст
    # хвостом, и кнопки прикрепляем к последнему сообщению (юзер просил
    # «кнопки всегда внизу»).
    long_caption = len(caption) > CAPTION_LIMIT
    if long_caption:
        short_caption = caption[: CAPTION_LIMIT - 3] + "…"
        photo_kb = None
    else:
        short_caption = caption
        photo_kb = kb

    file_size = 0
    try:
        file_size = _os.path.getsize(photo_path)
    except OSError:
        pass

    use_document = file_size > PHOTO_LIMIT
    msg = None
    if not use_document:
        try:
            msg = await notify(
                bot, "send_photo",
                settings.telegram_owner_chat_id,
                FSInputFile(photo_path),
                kind="hitl_photo",
                project_id=project.id,
                caption=short_caption,
                reply_markup=photo_kb,
            )
        except TelegramBadRequest as e:
            # «file ... too big for a photo» — фоллбэк в документ.
            if "too big for a photo" in str(e).lower():
                logger.warning(
                    "send_hitl_photo: {} > photo limit, шлю как document",
                    photo_path,
                )
                use_document = True
            else:
                raise

    if use_document:
        msg = await notify(
            bot, "send_document",
            settings.telegram_owner_chat_id,
            FSInputFile(photo_path),
            kind="hitl_photo",
            project_id=project.id,
            caption=short_caption,
            reply_markup=photo_kb,
        )
    assert msg is not None

    # Хвост подписи + кнопки последним сообщением.
    if long_caption:
        tail = caption[CAPTION_LIMIT - 3 :]
        chunks = [tail[i : i + 3800] for i in range(0, len(tail), 3800)] or [tail]
        for i, c in enumerate(chunks):
            is_last = i == len(chunks) - 1
            tail_msg = await notify(
                bot, "send_message",
                settings.telegram_owner_chat_id,
                c,
                kind="hitl_photo_tail",
                project_id=project.id,
                reply_markup=kb if is_last else None,
            )
            if is_last:
                msg = tail_msg

    req.tg_message_id = msg.message_id
    return req


async def send_hitl_video(
    bot: Bot | None,
    session: AsyncSession,
    project: Project,
    kind: HITLKind,
    video_path: str,
    caption: str,
    payload: dict | None = None,
    frame_id: int | None = None,
) -> HITLRequest:
    from aiogram.types import FSInputFile

    payload = dict(payload or {})
    payload.setdefault("video_path", video_path)
    req = await create_hitl(session, project, kind, payload=payload, frame_id=frame_id)
    if not settings.telegram_active:
        await publish_hitl_event(
            project.id,
            req.id,
            event_type="hitl_pending",
            payload={
                "kind": kind.value,
                "video_path": video_path,
                "caption": caption[:500],
                "frame_id": frame_id,
            },
        )
        return req
    if bot is None:
        raise RuntimeError("telegram_active but bot is None")
    msg = await notify(
        bot, "send_video",
        settings.telegram_owner_chat_id,
        FSInputFile(video_path),
        kind="hitl_video",
        project_id=project.id,
        caption=caption[:1000],
        reply_markup=_keyboard(req.id),
    )
    req.tg_message_id = msg.message_id
    return req



PUBLISH_CB_PREFIX = "hpub"
_PUBLISH_ACTIONS = ("yes", "no")
_PUBLISH_REASON_NO = "Оператор в Telegram нажал «Не публиковать»: площадка закрыта для этого файла."


def publish_keyboard(hitl_id: int) -> InlineKeyboardMarkup:
    from app.telegram import publish_texts as ptexts
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=ptexts.BTN_PUBLISH_YES, callback_data=f"{PUBLISH_CB_PREFIX}:{hitl_id}:yes"),
        InlineKeyboardButton(text=ptexts.BTN_PUBLISH_NO, callback_data=f"{PUBLISH_CB_PREFIX}:{hitl_id}:no"),
    ]])


def _matches(req: HITLRequest, sha256: str, caption_sha256: str) -> bool:
    p = req.payload or {}
    return p.get("sha256") == sha256 and p.get("caption_sha256") == caption_sha256


async def _pending_publish_requests(session: AsyncSession, project_id: int) -> list[HITLRequest]:
    return list((await session.execute(select(HITLRequest).where(
        HITLRequest.project_id == project_id, HITLRequest.kind == HITLKind.approve_publish,
        HITLRequest.decision == HITLDecision.pending).order_by(HITLRequest.id))).scalars().all())


async def request_publish_approval(bot: Bot | None, session: AsyncSession, project: Project, *,
                                   platforms: list[str], sha256: str, caption: str, caption_sha256: str,
                                   artifact_uuid: str | None = None) -> list[HITLRequest]:
    if not settings.telegram_active or bot is None or not platforms:
        return []
    from app.telegram import publish_texts as ptexts

    pending = await _pending_publish_requests(session, project.id)
    for old in pending:
        if _matches(old, sha256, caption_sha256):
            continue
        old.decision = HITLDecision.rejected
        old.decided_at = datetime.utcnow()
        old.payload = {**(old.payload or {}), "superseded": "new_file_or_caption"}
        if old.tg_message_id:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=settings.telegram_owner_chat_id, message_id=old.tg_message_id, reply_markup=None)
            except Exception:
                logger.debug("approve_publish #{}: не удалось убрать кнопки у устаревшего запроса", old.id)
    await session.flush()
    already = {(r.payload or {}).get("platform") for r in pending if _matches(r, sha256, caption_sha256)}
    made: list[HITLRequest] = []
    for platform in sorted(set(platforms) - already):
        payload = {"platform": platform, "sha256": sha256, "caption_sha256": caption_sha256,
                   "artifact_uuid": artifact_uuid, "caption": caption, "text": None}
        try:
            req = await send_hitl_text(
                bot, session, project, HITLKind.approve_publish,
                ptexts.publish_approval_title(project.id),
                ptexts.publish_approval_request_text(platform, sha256, caption),
                payload=payload, keyboard=publish_keyboard)
        except Exception:
            logger.exception("approve_publish: сообщение по {} (проект #{}) не отправлено", platform, project.id)
            for orphan in await _pending_publish_requests(session, project.id):
                if (orphan.payload or {}).get("platform") == platform and orphan.tg_message_id is None                         and _matches(orphan, sha256, caption_sha256):
                    await session.delete(orphan)
            await session.flush()
            continue
        made.append(req)
    await session.flush()
    return made


async def recompute_publication_state(session: AsyncSession, project: Project, sha: str) -> dict:
    from app.bots.publishers import ALL_PUBLISHERS
    from app.orchestrator.steps import publish as publish_step
    from app.services import publications_store as pstore

    platforms = [c.platform for c in ALL_PUBLISHERS]
    final = await publish_step._latest_final(session, project)
    rows = await pstore.list_for(session, project.id, sha)
    caption_sha = publish_step.caption_sha256(publish_step.caption_for(project))
    approvals = await pstore.list_approvals(session, project.id, sha, caption_sha)
    state = await publish_step.apply_project_state(
        session, project, rows=rows, platforms=platforms, sha=sha,
        artifact_uuid=final.uuid if final is not None else None,
        approved={p for p, a in approvals.items() if a["valid"]})
    publish_step._sync_meta(project, rows, platforms)
    await session.commit()
    return state


async def grant_publish_approvals(session: AsyncSession, project: Project, platforms: list[str], *, sha256: str,
                                  caption_sha256: str, actor: str) -> dict:
    from app.models import ProjectStatus
    from app.orchestrator.steps import publish as publish_step
    from app.services import publications_store as pstore

    granted: list[str] = []
    for p in dict.fromkeys(platforms):
        await pstore.grant_approval(session, project_id=project.id, platform=p, sha256=sha256,
                                    caption_sha256=caption_sha256, approved_by=actor)
        granted.append(p)
    queued = False
    if settings.social_publish_enabled and project.status is ProjectStatus.assembled:
        publish_step._set_markers(project, **{publish_step._AWAITING_KEY: None, publish_step._HUMAN_KEY: None,
                                              publish_step._CLOSED_KEY: None})
        project.status = ProjectStatus.publishing
        queued = True
    await session.commit()
    return {"approved": granted, "sha256": sha256, "queued_for_publish_step": queued,
            "publish_enabled": bool(settings.social_publish_enabled)}


@dataclass
class PublishDecision:
    ok: bool
    code: str
    text: str
    action: str | None = None
    platform: str | None = None
    project_id: int | None = None
    request_id: int | None = None
    hide_buttons: bool = False
    badge: str | None = None


async def _audit_tg(*, user_id: int | None, path: str, project_id: int | None, status: int, started: float) -> None:
    from app.services import operator_audit as oa
    try:
        await oa.record({
            "ts": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" "),
            "request_id": uuid.uuid4().hex, "method": "TELEGRAM", "path": path[:500],
            "route_template": "telegram:approve_publish", "project_id": project_id,
            "actor": f"telegram:{user_id}"[:120], "status_code": status,
            "duration_ms": int((time.perf_counter() - started) * 1000), "body_sha256": None,
            "outcome": oa.outcome_for(status)})
    except Exception:
        logger.exception("approve_publish: аудит не записан")


def _refusal(code: str, **kw) -> PublishDecision:
    from app.telegram import publish_texts as ptexts
    return PublishDecision(False, code, ptexts.publish_approval_refusal_text(code), **kw)


async def _close_request(session: AsyncSession, hitl_id: int, decision: HITLDecision, **extra) -> bool:
    req = await session.get(HITLRequest, hitl_id)
    payload = {**((req.payload if req is not None else None) or {}), **extra}
    upd = await session.execute(update(HITLRequest).where(
        HITLRequest.id == hitl_id, HITLRequest.decision == HITLDecision.pending).values(
        decision=decision, decided_at=datetime.utcnow(), payload=payload))
    await session.commit()
    return upd.rowcount == 1


async def _staleness(session: AsyncSession, project: Project, payload: dict) -> str | None:
    from app.bots.publishers import ALL_PUBLISHERS
    from app.models import ProjectStatus
    from app.orchestrator.steps import publish as publish_step

    if project.status not in (ProjectStatus.assembled, ProjectStatus.publishing, ProjectStatus.published):
        return "project_not_ready"
    if payload.get("platform") not in [c.platform for c in ALL_PUBLISHERS]:
        return "unknown_platform"
    final = await publish_step._latest_final(session, project)
    if final is None:
        return "no_final_video"
    sha = await publish_step._file_sha(final.path)
    if sha == "no-file":
        return "file_unreadable"
    if sha != payload.get("sha256"):
        return "stale_artifact"
    if publish_step.caption_sha256(publish_step.caption_for(project)) != payload.get("caption_sha256"):
        return "stale_caption"
    return None


async def decide_publish_approval(session: AsyncSession, hitl_id: int, action: str, tg_user_id: int | None
                                  ) -> PublishDecision:
    from app.services import publications_store as pstore
    from app.telegram import publish_texts as ptexts

    started = time.perf_counter()
    path = f"/telegram/hitl/{hitl_id}/approve_publish/{action}"

    async def done(res: PublishDecision, status: int) -> PublishDecision:
        await _audit_tg(user_id=tg_user_id, path=path, project_id=res.project_id, status=status, started=started)
        return res

    if tg_user_id is None or tg_user_id != settings.telegram_owner_chat_id:
        return await done(_refusal("forbidden", request_id=hitl_id), 403)
    if action not in _PUBLISH_ACTIONS:
        return await done(_refusal("bad_callback", request_id=hitl_id), 400)
    req = await session.get(HITLRequest, hitl_id)
    if req is None or req.kind is not HITLKind.approve_publish:
        return await done(_refusal("not_found", request_id=hitl_id), 404)
    payload = dict(req.payload or {})
    platform, pid = payload.get("platform"), req.project_id
    ids = dict(action=action, platform=platform, project_id=pid, request_id=hitl_id)
    if req.decision is not HITLDecision.pending:
        return await done(_refusal("already_decided", hide_buttons=True, **ids), 409)
    project = await session.get(Project, pid)
    if project is None:
        return await done(_refusal("not_found", **ids), 404)

    stale = await _staleness(session, project, payload)
    if stale in ("stale_artifact", "stale_caption", "no_final_video", "file_unreadable"):
        await _close_request(session, hitl_id, HITLDecision.rejected, superseded=stale)
        return await done(_refusal(stale, hide_buttons=True, badge=ptexts.publish_approval_refusal_text(stale),
                                   **ids), 409)
    if stale is not None:
        return await done(_refusal(stale, **ids), 409)

    actor = f"telegram:{tg_user_id}"
    sha, csha = str(payload["sha256"]), str(payload["caption_sha256"])
    chosen = HITLDecision.approved if action == "yes" else HITLDecision.rejected
    if not await _close_request(session, hitl_id, chosen, decided_by=actor, decided_action=action):
        return await done(_refusal("already_decided", hide_buttons=True, **ids), 409)
    try:
        if action == "yes":
            res = await grant_publish_approvals(session, project, [platform], sha256=sha, caption_sha256=csha,
                                                actor=actor)
            queued, enabled = res["queued_for_publish_step"], res["publish_enabled"]
        else:
            await _close_platform(session, project, platform, sha256=sha, actor=actor)
            await recompute_publication_state(session, project, sha)
            queued, enabled = False, bool(settings.social_publish_enabled)
    except pstore.PublicationRefused as exc:
        await session.rollback()
        await _mark_refused(session, hitl_id, exc.code)
        return await done(PublishDecision(False, exc.code, exc.message, hide_buttons=True, badge=exc.message, **ids),
                          409)
    text_ru = ptexts.publish_approval_done_text(pid, platform, action, actor, queued=queued, publish_enabled=enabled)
    return await done(PublishDecision(True, "ok", text_ru, hide_buttons=True, badge=text_ru, **ids), 200)


async def _mark_refused(session: AsyncSession, hitl_id: int, code: str) -> None:
    req = await session.get(HITLRequest, hitl_id)
    if req is not None:
        req.decision = HITLDecision.rejected
        req.payload = {**(req.payload or {}), "refused": code}
        await session.commit()


async def _close_platform(session: AsyncSession, project: Project, platform: str, *, sha256: str,
                          actor: str) -> None:
    from app.orchestrator.steps import publish as publish_step
    from app.services import publications_store as pstore

    rows = await pstore.list_for(session, project.id, sha256)
    if not any(r["platform"] == platform for r in rows):
        final = await publish_step._latest_final(session, project)
        await pstore.mark_skipped(session, project_id=project.id, platform=platform, sha256=sha256,
                                  artifact_uuid=final.uuid if final is not None else None,
                                  reason="operator_declined_telegram")
    await pstore.mark_failed_manual(session, project_id=project.id, platform=platform, sha256=sha256,
                                    actor=actor, reason=_PUBLISH_REASON_NO, allow_republish=False)


async def handle_publish_approval_callback(session: AsyncSession, data: str | None, tg_user_id: int | None
                                           ) -> PublishDecision:
    started = time.perf_counter()
    try:
        prefix, hitl_id_s, action = (data or "").split(":", 2)
        hitl_id = int(hitl_id_s)
        if prefix != PUBLISH_CB_PREFIX:
            raise ValueError(prefix)
    except ValueError:
        await _audit_tg(user_id=tg_user_id, path="/telegram/hitl/?/approve_publish", project_id=None, status=400,
                        started=started)
        return _refusal("bad_callback")
    return await decide_publish_approval(session, hitl_id, action, tg_user_id)
