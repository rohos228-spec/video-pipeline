"""HITL-гейты: создаём запрос на подтверждение в Telegram, шлём артефакт с
инлайн-кнопками и ждём решения пользователя (polling по БД).
"""

from __future__ import annotations

import asyncio

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import HITLDecision, HITLKind, HITLRequest, Project
from app.settings import settings


def _keyboard(hitl_id: int, *, allow_edit: bool = False) -> InlineKeyboardMarkup:
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
    rows.append([
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"hitl:{hitl_id}:reject"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
    return req


async def send_hitl_text(
    bot: Bot,
    session: AsyncSession,
    project: Project,
    kind: HITLKind,
    title: str,
    text: str,
    payload: dict | None = None,
    frame_id: int | None = None,
) -> HITLRequest:
    import html as _html

    req = await create_hitl(session, project, kind, payload=payload, frame_id=frame_id)
    # Используем HTML parse_mode и экранируем произвольный текст — так Telegram
    # не спотыкается о «грязный» markdown из LLM-ответа (звёздочки, скобки,
    # бэктики в непредвидимых местах).
    body = f"<b>{_html.escape(title)}</b>\n\n{_html.escape(text)}"
    chunks = [body[i : i + 3800] for i in range(0, len(body), 3800)] or [body]
    msg = await bot.send_message(
        settings.telegram_owner_chat_id,
        chunks[0],
        parse_mode="HTML",
        reply_markup=_keyboard(req.id),
    )
    for c in chunks[1:]:
        await bot.send_message(
            settings.telegram_owner_chat_id, c, parse_mode="HTML"
        )
    req.tg_message_id = msg.message_id
    return req


async def send_hitl_photo(
    bot: Bot,
    session: AsyncSession,
    project: Project,
    kind: HITLKind,
    photo_path: str,
    caption: str,
    payload: dict | None = None,
    frame_id: int | None = None,
    allow_edit: bool = False,
) -> HITLRequest:
    from aiogram.types import FSInputFile

    req = await create_hitl(session, project, kind, payload=payload, frame_id=frame_id)
    msg = await bot.send_photo(
        settings.telegram_owner_chat_id,
        FSInputFile(photo_path),
        caption=caption[:1000],
        reply_markup=_keyboard(req.id, allow_edit=allow_edit),
    )
    req.tg_message_id = msg.message_id
    return req


async def send_hitl_video(
    bot: Bot,
    session: AsyncSession,
    project: Project,
    kind: HITLKind,
    video_path: str,
    caption: str,
    payload: dict | None = None,
    frame_id: int | None = None,
) -> HITLRequest:
    from aiogram.types import FSInputFile

    req = await create_hitl(session, project, kind, payload=payload, frame_id=frame_id)
    msg = await bot.send_video(
        settings.telegram_owner_chat_id,
        FSInputFile(video_path),
        caption=caption[:1000],
        reply_markup=_keyboard(req.id),
    )
    req.tg_message_id = msg.message_id
    return req


async def wait_for_decision(hitl_id: int, *, poll_seconds: float = 2.0) -> HITLDecision:
    """Блокирует текущую корутину, пока HITL не будет принят/отклонён/regen."""
    logger.info("waiting for HITL {}", hitl_id)
    while True:
        async with session_scope() as s:
            req = (
                await s.execute(select(HITLRequest).where(HITLRequest.id == hitl_id))
            ).scalar_one_or_none()
            if req is None:
                raise RuntimeError(f"HITL #{hitl_id} исчез из БД")
            if req.decision is not HITLDecision.pending:
                logger.info("HITL {} decided: {}", hitl_id, req.decision.value)
                return req.decision
        await asyncio.sleep(poll_seconds)
