"""HITL-гейты: создаём запрос на подтверждение в Telegram, шлём артефакт с
инлайн-кнопками и ждём решения пользователя (polling по БД).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import HITLDecision, HITLKind, HITLRequest, Project
from app.settings import settings

# Kinds, у которых в payload['photo_path'] лежит файл на диске. Используется
# для очистки файла при regen/reject (см. `delete_hitl_artifact_file`).
_PHOTO_KINDS: frozenset[HITLKind] = frozenset({
    HITLKind.approve_images,
    HITLKind.approve_hero,
})


def delete_hitl_artifact_file(req: HITLRequest) -> bool:
    """Удаляет файл, на который ссылается HITL (payload['photo_path']).

    Политика: при regen/reject не копим разные варианты одной и той же
    карточки в `scenes/` (или `characters/`). Подходит только для
    photo-kinds — для текстовых/видео HITL ничего не делает.

    Возвращает True если файл был и удалось удалить.
    """
    if req.kind not in _PHOTO_KINDS:
        return False
    payload = req.payload or {}
    photo_path = payload.get("photo_path")
    if not photo_path:
        return False
    p = Path(str(photo_path))
    try:
        if p.is_file():
            p.unlink()
            logger.info(
                "hitl: удалил файл {} (HITL #{} kind={} decision={})",
                p, req.id, req.kind.value,
                req.decision.value if req.decision else "?",
            )
            return True
    except OSError as e:
        logger.warning(
            "hitl: не удалось удалить {} (HITL #{}): {}",
            p, req.id, e,
        )
    return False


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

    # Кнопки прикрепляем ТОЛЬКО к последнему сообщению — чтобы они всегда
    # были снизу. Иначе при дроблении на куски кнопки прилипают к первой
    # части, и ниже идёт «голый» хвост текста.
    msg = None
    for i, c in enumerate(chunks):
        is_last = i == len(chunks) - 1
        msg = await bot.send_message(
            settings.telegram_owner_chat_id,
            c,
            parse_mode="HTML",
            reply_markup=_keyboard(req.id) if is_last else None,
        )
    assert msg is not None
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
            msg = await bot.send_photo(
                settings.telegram_owner_chat_id,
                FSInputFile(photo_path),
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
        msg = await bot.send_document(
            settings.telegram_owner_chat_id,
            FSInputFile(photo_path),
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
            tail_msg = await bot.send_message(
                settings.telegram_owner_chat_id,
                c,
                reply_markup=kb if is_last else None,
            )
            if is_last:
                msg = tail_msg

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
