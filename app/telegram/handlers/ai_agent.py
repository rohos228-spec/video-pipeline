"""Telegram-handler для AI-агента (`/ai`, `/ai pro`, `/ai cancel`, `/ai status`).

Командой `/ai <запрос>` owner начинает сессию с AI-агентом. Агент:
- Использует tools (read_file, search_code, db_query, edit_file, ...) для
  работы с этим репо.
- На каждую правку файла шлёт diff-карточку с кнопками ✅/🔁/✏️/❌.
- В конце шлёт final_answer.

См. AGENTS.md §16 и app/ai_agent/README.

Доступ — ТОЛЬКО `chat_id == TELEGRAM_OWNER_CHAT_ID`.
"""

from __future__ import annotations

import asyncio
import html
import json
from typing import Any

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from loguru import logger

from app.ai_agent import get_config
from app.ai_agent.audit import (
    append_message,
    close_session,
    create_session,
    record_tool_call,
    update_tool_call_status,
)
from app.ai_agent.client import AIClient
from app.ai_agent.loop import create_runtime_session, run_loop
from app.ai_agent.session import RuntimeSession
from app.db import session_scope
from app.models import (
    AIMessageRole,
    AISession,
    AISessionMode,
    AISessionStatus,
    AIToolCallStatus,
)
from app.settings import settings
from app.telegram.callback_registry import CB
from app.telegram.keyboards import (
    kb_hitl_4buttons,
    kb_session_summary,
    make_callback,
)

router = Router(name="ai_agent")


# ────────────────────────────────────────────────────────────────────────────
# Глобальное состояние активных сессий (in-memory).
# Один владелец → одна активная сессия в момент времени.
# ────────────────────────────────────────────────────────────────────────────


_active_sessions: dict[int, RuntimeSession] = {}  # chat_id → session
_active_tasks: dict[int, asyncio.Task] = {}  # chat_id → loop task
_pending_hitl_futures: dict[int, asyncio.Future] = {}  # tool_call_id (db) → future
_clarification_waits: dict[int, int] = {}  # chat_id → tool_call_db_id (ждём текст-уточнение)


# ────────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────────


def _is_owner(chat_id: int) -> bool:
    return chat_id == settings.telegram_owner_chat_id


def _summary_kb(session_id: int) -> InlineKeyboardMarkup:
    """Прогресс активной сессии: ⏹ Отменить + 📊 Status."""
    return kb_session_summary(
        cancel_callback=make_callback(CB.AI_CANCEL, session_id),
        status_callback=make_callback(CB.AI_STATUS, session_id),
        cancel_text="⏹ Отменить сессию",
    )


def _hitl_kb(tool_call_db_id: int) -> InlineKeyboardMarkup:
    """4-кнопочная HITL-карточка (AGENTS.md §10 инвариант)."""
    return kb_hitl_4buttons(
        approve_cb=make_callback(CB.AI_APPROVE, tool_call_db_id),
        regen_cb=make_callback(CB.AI_REGEN, tool_call_db_id),
        clarify_cb=make_callback(CB.AI_CLARIFY, tool_call_db_id),
        reject_cb=make_callback(CB.AI_REJECT, tool_call_db_id),
    )


def _parse_callback_id(callback: CallbackQuery) -> int | None:
    """Извлечь id из callback_data вида 'ai:foo:42' → 42, или None."""
    data = callback.data
    if not data:
        return None
    parts = data.split(":")
    if len(parts) < 3:
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


async def _edit_markup_safe(
    callback: CallbackQuery,
    markup: InlineKeyboardMarkup,
) -> None:
    """Обновить reply_markup на callback.message, если он Message."""
    from aiogram.types import Message  # noqa: PLC0415

    msg = callback.message
    if isinstance(msg, Message):
        try:
            await msg.edit_reply_markup(reply_markup=markup)
        except Exception:  # noqa: BLE001
            pass


def _format_args_preview(tool_name: str, args: dict) -> str:
    """Превью args для HITL-карточки. Для edit_file делаем diff-like."""
    if tool_name == "edit_file":
        path = html.escape(str(args.get("path", "")))
        old = html.escape(str(args.get("old_string", "")))
        new = html.escape(str(args.get("new_string", "")))
        # Обрезаем для TG (4096 байт лимит на сообщение).
        for s, var in [(old, "old"), (new, "new")]:
            if len(s) > 600:
                pass  # handled below
        if len(old) > 600:
            old = old[:600] + "...[truncated]"
        if len(new) > 600:
            new = new[:600] + "...[truncated]"
        return (
            f"📝 <b>Правка файла</b> <code>{path}</code>\n\n"
            f"<b>− Было:</b>\n<pre>{old}</pre>\n\n"
            f"<b>+ Стало:</b>\n<pre>{new}</pre>"
        )
    if tool_name == "write_file":
        path = html.escape(str(args.get("path", "")))
        content = str(args.get("content", ""))
        if len(content) > 1000:
            content = content[:1000] + "\n...[truncated]"
        return (
            f"📄 <b>Создать файл</b> <code>{path}</code>\n\n"
            f"<pre>{html.escape(content)}</pre>"
        )
    if tool_name == "git_branch":
        name = html.escape(str(args.get("name", "")))
        return f"🌿 <b>Создать ветку</b> <code>{name}</code>"
    if tool_name == "git_commit":
        msg = html.escape(str(args.get("message", "")))
        paths = args.get("paths") or []
        paths_text = (
            f"\n<i>Пути:</i> {html.escape(json.dumps(paths))}" if paths else ""
        )
        return f"📦 <b>Коммит</b>\n<pre>{msg}</pre>{paths_text}"
    if tool_name == "gh_pr_create":
        title = html.escape(str(args.get("title", "")))
        body = html.escape(str(args.get("body", "")))[:500]
        return f"🔀 <b>Открыть PR</b>\n<i>Title:</i> {title}\n\n{body}"
    # default
    return (
        f"🔧 <b>{html.escape(tool_name)}</b>\n"
        f"<pre>{html.escape(json.dumps(args, ensure_ascii=False, indent=2)[:1000])}</pre>"
    )


# ────────────────────────────────────────────────────────────────────────────
# Commands
# ────────────────────────────────────────────────────────────────────────────


@router.message(Command("ai"))
async def cmd_ai(message: Message, command: CommandObject) -> None:
    if not message.from_user or not _is_owner(message.from_user.id):
        await message.answer("⛔ Только владелец может пользоваться /ai.")
        return

    args_text = (command.args or "").strip()
    if not args_text:
        await message.answer(
            "<b>🤖 AI-агент</b>\n\n"
            "Использование:\n"
            "<code>/ai &lt;вопрос или задача&gt;</code> — старт сессии.\n"
            "<code>/ai pro &lt;запрос&gt;</code> — на gpt-4o (умнее, дороже ×3).\n"
            "<code>/ai claude &lt;запрос&gt;</code> — на claude-opus-4.1 (рефакторинги).\n"
            "<code>/ai auto &lt;запрос&gt;</code> — в feature-ветке без HITL.\n"
            "<code>/ai cancel</code> — остановить текущую сессию.\n"
            "<code>/ai status</code> — статус активной сессии.\n"
            "<code>/ai history</code> — последние 10 сессий.\n"
            "<code>/ai dump &lt;id&gt;</code> — дамп сессии по ID.\n\n"
            "Что умеет:\n"
            "• читать файлы, искать в коде, смотреть БД;\n"
            "• править файлы (требует ✅ от тебя);\n"
            "• запускать тесты, ruff, mypy;\n"
            "• делать коммиты, открывать PR (тоже с ✅).\n\n"
            "Доступно ~14 read-only + 5 edit tools. Лимиты: 200k токенов "
            "и 30 шагов на сессию.",
            parse_mode="HTML",
        )
        return

    # Парсим подкоманды
    chat_id = message.chat.id
    cfg = get_config()
    if not cfg.is_configured:
        await message.answer(
            "⚠️ AI-агент не сконфигурирован. Добавь в <code>.env</code>:\n"
            "<pre>ORCHESTRATOR_AI_API_KEY=sk-aitunnel-...</pre>\n"
            "Получить ключ: https://aitunnel.ru/",
            parse_mode="HTML",
        )
        return

    # Подкоманды
    parts = args_text.split(maxsplit=1)
    first = parts[0].lower() if parts else ""

    if first == "cancel":
        await _cmd_cancel(message)
        return
    if first == "status":
        await _cmd_status(message)
        return
    if first == "history":
        await _cmd_history(message)
        return
    if first == "dump":
        await _cmd_dump(message, parts[1] if len(parts) > 1 else "")
        return

    # Выбираем модель
    model = cfg.default_model
    mode = AISessionMode.hitl_edit
    query = args_text

    if first == "pro":
        model = cfg.pro_model
        query = parts[1] if len(parts) > 1 else ""
    elif first == "claude":
        model = cfg.code_model
        query = parts[1] if len(parts) > 1 else ""
    elif first == "auto":
        mode = AISessionMode.auto
        query = parts[1] if len(parts) > 1 else ""

    if not query.strip():
        await message.answer("⚠️ Пустой запрос. Пример: <code>/ai объясни как работает шаг split_frames</code>", parse_mode="HTML")
        return

    # Проверка что нет активной сессии
    if chat_id in _active_sessions:
        await message.answer(
            "⚠️ Уже идёт активная сессия. Дождись завершения или "
            "<code>/ai cancel</code>.",
            parse_mode="HTML",
        )
        return

    # Создаём БД-сессию + runtime
    async with session_scope() as db:
        db_session = await create_session(
            db,
            chat_id=chat_id,
            initial_query=query,
            model=model,
            mode=mode,
        )
        db_session_id = db_session.id

    runtime = await create_runtime_session(
        cfg, chat_id=chat_id, initial_query=query,
        model=model, mode=mode, db_id=db_session_id,
    )
    _active_sessions[chat_id] = runtime

    # Шлём начальное сообщение (потом будем редактировать прогресс)
    initial_msg = await message.answer(
        f"🤖 <b>AI-сессия #{db_session_id}</b> запущена\n"
        f"Модель: <code>{model}</code>\n"
        f"Режим: <code>{mode.value}</code>\n\n"
        f"<i>{html.escape(query[:300])}</i>\n\n"
        f"⏳ Думаю…",
        parse_mode="HTML",
        reply_markup=_summary_kb(db_session_id),
    )

    # Записываем summary_message_id для прогресса
    async with session_scope() as db:
        db_obj = await db.get(AISession, db_session_id)
        if db_obj:
            db_obj.summary_message_id = initial_msg.message_id

    # Запускаем loop в фоне
    task = asyncio.create_task(
        _run_session_task(runtime, message.bot, initial_msg.message_id)
    )
    _active_tasks[chat_id] = task


async def _cmd_cancel(message: Message) -> None:
    chat_id = message.chat.id
    runtime = _active_sessions.get(chat_id)
    if runtime is None:
        await message.answer("Нет активной сессии.")
        return
    runtime.cancel()
    task = _active_tasks.get(chat_id)
    if task and not task.done():
        task.cancel()
    await message.answer(f"⏹ Сессия #{runtime.db_id} отменяется…")


async def _cmd_status(message: Message) -> None:
    chat_id = message.chat.id
    runtime = _active_sessions.get(chat_id)
    if runtime is None:
        await message.answer("Нет активной сессии.")
        return
    await message.answer(
        "<pre>" + html.escape(runtime.summary_text()) + "</pre>",
        parse_mode="HTML",
    )


async def _cmd_history(message: Message) -> None:
    """Список последних 10 сессий."""
    from sqlalchemy import select

    async with session_scope() as db:
        rows = (
            await db.execute(
                select(AISession)
                .where(AISession.chat_id == message.chat.id)
                .order_by(AISession.id.desc())
                .limit(10)
            )
        ).scalars().all()

    if not rows:
        await message.answer("История пуста.")
        return

    lines = ["<b>📜 Последние 10 AI-сессий:</b>"]
    for s in rows:
        ts = s.started_at.strftime("%m-%d %H:%M") if s.started_at else "?"
        cost = f"{s.cost_rub:.2f}₽" if s.cost_rub else "—"
        title = html.escape(s.initial_query[:60])
        lines.append(
            f"#{s.id} [{s.status.value}] {ts} {cost} — {title}"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


# ────────────────────────────────────────────────────────────────────────────
# Session task (фоновый run_loop)
# ────────────────────────────────────────────────────────────────────────────


async def _cmd_dump(message: Message, session_id_str: str) -> None:
    """Дамп конкретной AI-сессии (по ID) — последние messages + tool_calls."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    try:
        session_id = int(session_id_str.strip())
    except (ValueError, AttributeError):
        await message.answer(
            "Использование: <code>/ai dump &lt;session_id&gt;</code>\n"
            "<code>/ai history</code> — список ID.",
            parse_mode="HTML",
        )
        return

    async with session_scope() as db:
        stmt = (
            select(AISession)
            .where(AISession.id == session_id, AISession.chat_id == message.chat.id)
            .options(
                selectinload(AISession.messages),
                selectinload(AISession.tool_calls),
            )
        )
        s = (await db.execute(stmt)).scalar_one_or_none()

    if s is None:
        await message.answer(f"Сессия #{session_id} не найдена.")
        return

    lines = [
        f"<b>🤖 AI-сессия #{s.id}</b>",
        f"<code>{s.status.value}</code> · <code>{s.mode.value}</code> · <code>{s.model}</code>",
        f"Шагов: {s.step_count}",
        f"Токены: {s.total_tokens_in}+{s.total_tokens_out} (~{s.cost_rub:.2f}₽)",
        "",
        f"<b>Запрос:</b> <i>{html.escape((s.initial_query or '')[:200])}</i>",
        "",
        f"<b>Tool calls ({len(s.tool_calls)}):</b>",
    ]
    for tc in s.tool_calls[-15:]:
        args_str = html.escape(json.dumps(tc.args_json or {}, ensure_ascii=False)[:80])
        lines.append(f"  • <code>{tc.tool_name}</code> [{tc.status.value}] {args_str}")
    if s.final_answer:
        lines.extend([
            "",
            f"<b>Итог:</b>\n<pre>{html.escape((s.final_answer or '')[:1000])}</pre>",
        ])
    text_out = "\n".join(lines)
    await message.answer(text_out[:4000], parse_mode="HTML")


async def _run_session_task(
    runtime: RuntimeSession, bot: Any, summary_msg_id: int
) -> None:
    """Запустить run_loop и отчитаться по результату."""
    cfg = get_config()
    client = AIClient(cfg)

    async def hitl_callback(tool_name: str, args: dict, sess: RuntimeSession) -> dict:
        """HITL: создаём AIToolCall с pending, шлём карточку, ждём callback."""
        return await _ask_owner_for_hitl(
            bot, runtime.chat_id, runtime.db_id, tool_name, args
        )

    async def progress_callback(sess: RuntimeSession) -> None:
        """Обновляем сводное сообщение."""
        try:
            await bot.edit_message_text(
                chat_id=sess.chat_id,
                message_id=summary_msg_id,
                text=(
                    f"🤖 <b>AI-сессия #{sess.db_id}</b>\n"
                    f"Шагов: {sess.step_count} | "
                    f"токены: {sess.tokens_in}+{sess.tokens_out} "
                    f"(~{sess.estimate_cost():.2f}₽)\n"
                    f"⏳ Работаю…"
                ),
                parse_mode="HTML",
                reply_markup=_summary_kb(sess.db_id),
            )
        except Exception:  # noqa: BLE001
            pass  # message already edited / removed

    async def on_step_audit(sess: RuntimeSession, resp: Any) -> None:
        """Записываем assistant-сообщение в БД для аудита."""
        async with session_scope() as db:
            db_sess = await db.get(AISession, sess.db_id)
            if db_sess:
                await append_message(
                    db, db_sess,
                    role=AIMessageRole.assistant,
                    content=resp.content,
                    tool_calls=resp.tool_calls or None,
                    tokens_in=resp.prompt_tokens,
                    tokens_out=resp.completion_tokens,
                )
                db_sess.step_count = sess.step_count
                db_sess.cost_rub = sess.cost_rub or sess.estimate_cost()

    async def on_tool_call_audit(name: str, args: dict, tc_id: str, sess: RuntimeSession) -> None:
        async with session_scope() as db:
            db_sess = await db.get(AISession, sess.db_id)
            if db_sess:
                await record_tool_call(
                    db, db_sess, None,
                    openai_call_id=tc_id, tool_name=name, args=args,
                )

    try:
        await run_loop(
            runtime, client, cfg,
            hitl_callback=hitl_callback,
            progress_callback=progress_callback,
            on_step=on_step_audit,
            on_tool_call=on_tool_call_audit,
        )
    except asyncio.CancelledError:
        runtime.cancel()
        logger.info("ai_agent: session #{} cancelled by task.cancel()", runtime.db_id)
    except Exception as e:  # noqa: BLE001
        logger.exception("ai_agent: session #{} failed: {}", runtime.db_id, e)
        runtime.final_answer = f"❌ Ошибка: {e}"
        runtime.finished = True
    finally:
        # КРИТИЧНО: cleanup ВСЕГДА выполняется, даже если последующая отправка
        # сообщения или закрытие БД упадёт. Без этого после редкого сбоя
        # _active_sessions[chat_id] остаётся занятым → owner залочен и не может
        # стартовать новую /ai сессию до перезапуска бота.
        # (Применён фикс из PR #40, спасибо параллельному cursor-агенту.)
        _active_sessions.pop(runtime.chat_id, None)
        _active_tasks.pop(runtime.chat_id, None)

    # Финальное сообщение
    final_text = (
        f"🤖 <b>AI-сессия #{runtime.db_id} завершена</b>\n\n"
        + html.escape(runtime.summary_text())
    )
    try:
        await bot.edit_message_text(
            chat_id=runtime.chat_id,
            message_id=summary_msg_id,
            text=final_text[:4000],
            parse_mode="HTML",
        )
    except Exception:  # noqa: BLE001
        # Inner try: если fallback send_message тоже упадёт (Telegram flood,
        # network), просто залогируем — не пробрасываем выше, чтобы DB-close
        # ниже выполнился.
        try:
            await bot.send_message(
                runtime.chat_id, final_text[:4000], parse_mode="HTML"
            )
        except Exception:  # noqa: BLE001
            logger.warning(
                "ai_agent: session #{} — не удалось доставить финальное сообщение",
                runtime.db_id,
            )

    # Закрыть в БД
    final_status = (
        AISessionStatus.cancelled
        if runtime.cancelled
        else AISessionStatus.completed
        if runtime.finished
        else AISessionStatus.failed
    )
    try:
        async with session_scope() as db:
            db_sess = await db.get(AISession, runtime.db_id)
            if db_sess:
                db_sess.step_count = runtime.step_count
                db_sess.cost_rub = runtime.cost_rub or runtime.estimate_cost()
                await close_session(
                    db, db_sess,
                    status=final_status,
                    final_answer=runtime.final_answer,
                )
    except Exception:  # noqa: BLE001
        # DB может упасть (SQLite lock, диск, ...) — не блокируем выход
        # из task. Cleanup уже сделан в finally выше.
        logger.exception(
            "ai_agent: session #{} — ошибка записи финального статуса в БД",
            runtime.db_id,
        )


async def _ask_owner_for_hitl(
    bot: Any, chat_id: int, session_id: int, tool_name: str, args: dict
) -> dict:
    """Создать AIToolCall(pending), послать карточку, ждать callback owner'а."""

    # Создать запись в БД
    async with session_scope() as db:
        from app.models import AIToolCall
        call = AIToolCall(
            session_id=session_id,
            openai_call_id=f"hitl_{session_id}_{tool_name}_{asyncio.get_event_loop().time()}",
            tool_name=tool_name,
            args_json=args,
            status=AIToolCallStatus.pending,
        )
        db.add(call)
        await db.flush()
        tool_call_db_id = call.id

    # Послать карточку
    preview = _format_args_preview(tool_name, args)
    msg = await bot.send_message(
        chat_id,
        preview[:4000],
        parse_mode="HTML",
        reply_markup=_hitl_kb(tool_call_db_id),
    )

    # Запомнить hitl_message_id
    async with session_scope() as db:
        from app.models import AIToolCall
        existing_call: AIToolCall | None = await db.get(AIToolCall, tool_call_db_id)
        if existing_call:
            existing_call.hitl_message_id = msg.message_id

    # Создать future и ждать
    future: asyncio.Future = asyncio.get_event_loop().create_future()
    _pending_hitl_futures[tool_call_db_id] = future

    cfg = get_config()
    try:
        result = await asyncio.wait_for(
            future, timeout=cfg.hitl_timeout_sec
        )
    except TimeoutError:
        await bot.send_message(
            chat_id,
            f"⏱ Таймаут HITL ({cfg.hitl_timeout_sec // 60} мин). "
            "Считаю отказом.",
        )
        result = {"decision": "rejected", "reason": "timeout"}
    finally:
        _pending_hitl_futures.pop(tool_call_db_id, None)

    return result


# ────────────────────────────────────────────────────────────────────────────
# Callback handlers (✅/🔁/✏️/❌)
# ────────────────────────────────────────────────────────────────────────────


@router.callback_query(F.data.startswith(CB.AI_APPROVE.value + ":"))
async def cb_approve(callback: CallbackQuery) -> None:
    if not callback.from_user or not _is_owner(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    tool_call_id = _parse_callback_id(callback)
    if tool_call_id is None:
        await callback.answer("Битый callback_data", show_alert=True)
        return
    future = _pending_hitl_futures.get(tool_call_id)
    if future is None or future.done():
        await callback.answer("Уже обработано или таймаут.", show_alert=True)
        return
    future.set_result({"decision": "approved"})
    async with session_scope() as db:
        from app.models import AIToolCall
        call = await db.get(AIToolCall, tool_call_id)
        if call:
            await update_tool_call_status(
                db, call, status=AIToolCallStatus.approved,
            )
    await _edit_markup_safe(
        callback,
        InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="✅ применено", callback_data=CB.AI_NOOP.value)]]
        ),
    )
    await callback.answer("✅")


@router.callback_query(F.data.startswith(CB.AI_REJECT.value + ":"))
async def cb_reject(callback: CallbackQuery) -> None:
    if not callback.from_user or not _is_owner(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    tool_call_id = _parse_callback_id(callback)
    if tool_call_id is None:
        await callback.answer("Битый callback_data", show_alert=True)
        return
    future = _pending_hitl_futures.get(tool_call_id)
    if future is None or future.done():
        await callback.answer("Уже обработано.", show_alert=True)
        return
    future.set_result({"decision": "rejected", "reason": "owner rejected"})
    async with session_scope() as db:
        from app.models import AIToolCall
        call = await db.get(AIToolCall, tool_call_id)
        if call:
            await update_tool_call_status(
                db, call, status=AIToolCallStatus.rejected,
            )
    await _edit_markup_safe(
        callback,
        InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="❌ отклонено", callback_data=CB.AI_NOOP.value)]]
        ),
    )
    await callback.answer("❌")


@router.callback_query(F.data.startswith(CB.AI_REGEN.value + ":"))
async def cb_regen(callback: CallbackQuery) -> None:
    """🔁 = попросить LLM попробовать другой подход."""
    if not callback.from_user or not _is_owner(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    tool_call_id = _parse_callback_id(callback)
    if tool_call_id is None:
        await callback.answer("Битый callback_data", show_alert=True)
        return
    future = _pending_hitl_futures.get(tool_call_id)
    if future is None or future.done():
        await callback.answer("Уже обработано.", show_alert=True)
        return
    future.set_result({
        "decision": "rejected",
        "reason": "owner попросил попробовать другой подход (🔁 regen)",
    })
    async with session_scope() as db:
        from app.models import AIToolCall
        call = await db.get(AIToolCall, tool_call_id)
        if call:
            await update_tool_call_status(
                db, call, status=AIToolCallStatus.rejected,
                error="regen requested",
            )
    await _edit_markup_safe(
        callback,
        InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔁 regen", callback_data=CB.AI_NOOP.value)]]
        ),
    )
    await callback.answer("🔁")


@router.callback_query(F.data.startswith(CB.AI_CLARIFY.value + ":"))
async def cb_clarify(callback: CallbackQuery) -> None:
    """✏️ — owner отправляет текстовое уточнение, оно идёт в LLM."""
    if not callback.from_user or not _is_owner(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    tool_call_id = _parse_callback_id(callback)
    if tool_call_id is None:
        await callback.answer("Битый callback_data", show_alert=True)
        return
    if tool_call_id not in _pending_hitl_futures:
        await callback.answer("Уже обработано.", show_alert=True)
        return
    from aiogram.types import Message  # noqa: PLC0415

    msg = callback.message
    if not isinstance(msg, Message):
        await callback.answer("Сообщение недоступно", show_alert=True)
        return
    _clarification_waits[msg.chat.id] = tool_call_id
    await msg.reply(
        "✏️ Напиши уточнение текстом — оно попадёт в LLM как новый hint, "
        "а текущая правка будет отклонена. /ai cancel — отмена."
    )
    await callback.answer("✏️ жду текст")


@router.callback_query(F.data.startswith(CB.AI_CANCEL.value + ":"))
async def cb_cancel_session(callback: CallbackQuery) -> None:
    from aiogram.types import Message  # noqa: PLC0415

    if not callback.from_user or not _is_owner(callback.from_user.id):
        await callback.answer("⛔", show_alert=True)
        return
    msg = callback.message
    if not isinstance(msg, Message):
        await callback.answer("Сообщение недоступно", show_alert=True)
        return
    chat_id = msg.chat.id
    runtime = _active_sessions.get(chat_id)
    if runtime is None:
        await callback.answer("Нет активной сессии.", show_alert=True)
        return
    runtime.cancel()
    task = _active_tasks.get(chat_id)
    if task and not task.done():
        task.cancel()
    await callback.answer("⏹ Отменяется…")


@router.callback_query(F.data.startswith(CB.AI_STATUS.value + ":"))
async def cb_status(callback: CallbackQuery) -> None:
    from aiogram.types import Message  # noqa: PLC0415

    msg = callback.message
    if not isinstance(msg, Message):
        await callback.answer("Сообщение недоступно", show_alert=True)
        return
    chat_id = msg.chat.id
    runtime = _active_sessions.get(chat_id)
    if runtime is None:
        await callback.answer("Нет активной сессии.", show_alert=True)
        return
    await callback.answer(
        f"#{runtime.db_id}: шаг {runtime.step_count}, "
        f"{runtime.tokens_in}+{runtime.tokens_out} ток., "
        f"~{runtime.estimate_cost():.2f}₽",
        show_alert=True,
    )


@router.callback_query(F.data == CB.AI_NOOP.value)
async def cb_noop(callback: CallbackQuery) -> None:
    await callback.answer()


# ────────────────────────────────────────────────────────────────────────────
# Text reply для clarification (✏️ flow)
# ────────────────────────────────────────────────────────────────────────────


async def _is_awaiting_clarification(message: Message) -> bool:
    """Кастомный фильтр: handler срабатывает ТОЛЬКО когда ждём текст-уточнение.

    Это критично — без фильтра наш router перехватил бы все text-сообщения
    owner'а, ломая существующие handlers в bot.py.
    """
    return (
        message.from_user is not None
        and _is_owner(message.from_user.id)
        and message.chat.id in _clarification_waits
    )


@router.message(
    F.text & ~F.text.startswith("/"),
    _is_awaiting_clarification,
)
async def msg_text_clarification(message: Message) -> None:
    """Owner написал текстовое уточнение после ✏️ — передаём в LLM."""
    chat_id = message.chat.id
    tool_call_id = _clarification_waits.pop(chat_id, None)
    if tool_call_id is None:
        return  # race condition защита

    text = (message.text or "").strip()
    if not text:
        await message.answer("Пустое уточнение — отмена.")
        return

    future = _pending_hitl_futures.get(tool_call_id)
    if future is None or future.done():
        await message.answer("Сессия уже не ждёт уточнения.")
        return

    future.set_result({"decision": "clarified", "clarification": text})
    async with session_scope() as db:
        from app.models import AIToolCall
        call = await db.get(AIToolCall, tool_call_id)
        if call:
            await update_tool_call_status(
                db, call,
                status=AIToolCallStatus.rejected,
                owner_clarification=text,
            )
    await message.answer(f"✏️ передал LLM: «{html.escape(text[:200])}»", parse_mode="HTML")
