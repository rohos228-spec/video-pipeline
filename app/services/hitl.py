"""HITL-гейты: создаём запрос на подтверждение в Telegram, шлём артефакт с
инлайн-кнопками и ждём решения пользователя (polling по БД).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TypeVar

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import HITLDecision, HITLKind, HITLRequest, Project
from app.settings import settings

_T = TypeVar("_T")

# Сетевые ошибки, на которые имеет смысл переотправлять TG-сообщение.
# Включает aiogram-обёртку TelegramNetworkError, ошибки aiohttp/socks-прокси
# и системные OSError (например WinError 64/121 при флапе SOCKS5).
_NETWORK_EXC: tuple[type[BaseException], ...] = (
    TelegramNetworkError,
    asyncio.TimeoutError,
    OSError,  # ClientOSError, WinError 64/121, и т.п.
)

# Задержки между ретраями (сек). Сумма ≈ 10 минут — даём прокси/сети ожить.
_TG_RETRY_DELAYS: tuple[int, ...] = (2, 4, 8, 16, 30, 60, 60, 60, 60, 60, 60, 60)


async def _send_with_network_retry(
    coro_factory: Callable[[], Awaitable[_T]],
    *,
    op: str,
) -> _T:
    """Вызывает coro_factory() (фабрика корутин) с ретраями на сетевые ошибки.

    Зачем factory, а не готовая корутина: корутину можно await'ить только один
    раз. На каждой попытке нужен СВЕЖИЙ объект корутины — фабрика создаёт его
    заново через `lambda: bot.send_photo(...)`.

    После ВСЕХ попыток (≈10 минут) пробрасывает последнее исключение наверх.
    """
    last_exc: BaseException | None = None
    total = len(_TG_RETRY_DELAYS) + 1
    for attempt in range(1, total + 1):
        try:
            return await coro_factory()
        except _NETWORK_EXC as e:
            last_exc = e
            if attempt >= total:
                break
            delay = _TG_RETRY_DELAYS[attempt - 1]
            logger.warning(
                "TG {} попытка {}/{} провалена ({}: {}) — ретрай через {}с",
                op, attempt, total, type(e).__name__, e, delay,
            )
            await asyncio.sleep(delay)
    logger.error(
        "TG {}: исчерпал {} попыток, бросаю исключение", op, total,
    )
    assert last_exc is not None
    raise last_exc

# Kinds, у которых в payload лежит ссылка на файл на диске.
# `_PHOTO_KINDS` хранят путь в `payload['photo_path']`,
# `_VIDEO_KINDS` — в `payload['video_path']`. Используется для очистки
# файла при regen/reject (см. `delete_hitl_artifact_file`).
_PHOTO_KINDS: frozenset[HITLKind] = frozenset({
    HITLKind.approve_images,
    HITLKind.approve_hero,
})
_VIDEO_KINDS: frozenset[HITLKind] = frozenset({
    HITLKind.approve_videos,
})


def delete_hitl_artifact_file(req: HITLRequest) -> bool:
    """Удаляет файл, на который ссылается HITL.

    Для photo-kinds (`approve_images`, `approve_hero`) — `payload['photo_path']`.
    Для video-kinds (`approve_videos`) — `payload['video_path']`.

    Политика: при regen/reject не копим разные варианты одной и той же
    карточки в `scenes/` / `characters/` / `videos/`. Для текстовых
    HITL ничего не делает.

    Возвращает True если файл был и удалось удалить.
    """
    payload = req.payload or {}
    file_path: str | None = None
    if req.kind in _PHOTO_KINDS:
        file_path = payload.get("photo_path")
    elif req.kind in _VIDEO_KINDS:
        file_path = payload.get("video_path")
    if not file_path:
        return False
    p = Path(str(file_path))
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
        chunk_text = c
        chunk_kb = _keyboard(req.id) if is_last else None
        msg = await _send_with_network_retry(
            lambda chunk=chunk_text, kb=chunk_kb: bot.send_message(
                settings.telegram_owner_chat_id,
                chunk,
                parse_mode="HTML",
                reply_markup=kb,
            ),
            op="send_message(text-chunk)",
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
            msg = await _send_with_network_retry(
                lambda: bot.send_photo(
                    settings.telegram_owner_chat_id,
                    FSInputFile(photo_path),
                    caption=short_caption,
                    reply_markup=photo_kb,
                ),
                op=f"send_photo(frame={frame_id})",
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
        msg = await _send_with_network_retry(
            lambda: bot.send_document(
                settings.telegram_owner_chat_id,
                FSInputFile(photo_path),
                caption=short_caption,
                reply_markup=photo_kb,
            ),
            op=f"send_document(frame={frame_id})",
        )
    assert msg is not None

    # Хвост подписи + кнопки последним сообщением.
    if long_caption:
        tail = caption[CAPTION_LIMIT - 3 :]
        chunks = [tail[i : i + 3800] for i in range(0, len(tail), 3800)] or [tail]
        for i, c in enumerate(chunks):
            is_last = i == len(chunks) - 1
            chunk_text = c
            chunk_kb = kb if is_last else None
            tail_msg = await _send_with_network_retry(
                lambda chunk=chunk_text, _kb=chunk_kb: bot.send_message(
                    settings.telegram_owner_chat_id,
                    chunk,
                    reply_markup=_kb,
                ),
                op="send_message(photo-caption-tail)",
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
    allow_edit: bool = False,
) -> HITLRequest:
    """Шлёт видео-карточку с inline-кнопками HITL.

    `allow_edit=True` добавляет кнопку «✏️ Изменить промт» — используется
    в per-frame HITL на шаге generate_videos: юзер может правкой текста
    переписать `animation_prompt` для конкретного кадра.

    Путь к файлу сохраняется в `payload['video_path']` (если не задан
    явно), чтобы `delete_hitl_artifact_file` мог удалить файл при
    regen/reject — политика «не копим варианты одного кадра в папке».
    """
    from aiogram.types import FSInputFile

    payload = dict(payload or {})
    payload.setdefault("video_path", video_path)
    req = await create_hitl(session, project, kind, payload=payload, frame_id=frame_id)
    kb = _keyboard(req.id, allow_edit=allow_edit)
    msg = await _send_with_network_retry(
        lambda: bot.send_video(
            settings.telegram_owner_chat_id,
            FSInputFile(video_path),
            caption=caption[:1000],
            reply_markup=kb,
        ),
        op=f"send_video(frame={frame_id})",
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
