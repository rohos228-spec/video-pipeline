"""Telegram-handler для команды `/debug` (Phase G).

Owner может через `/debug` подкоманды смотреть рантайм-состояние проекта,
БД, AI-агента, без захода в SQLite/PowerShell.

Подкоманды:
    /debug status                — список активных проектов (status, last update)
    /debug project <id>          — детальный дамп одного проекта
    /debug locks                 — проекты с request_stop / зависшие
    /debug logs <id> [tail=50]   — последние строки лога этого проекта (если есть)
    /debug ai [session_id]       — список AI-сессий или одна по id
    /debug selftest              — быстрый прогон: CDP / FFmpeg / SQLite WAL / AI
    /debug api                   — статус локального orchestrator_api (если поднят)

Доступ — ТОЛЬКО `chat_id == TELEGRAM_OWNER_CHAT_ID`.
"""

from __future__ import annotations

import asyncio
import html
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

import aiohttp
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import session_scope
from app.models import (
    AIMessage,
    AISession,
    AIToolCall,
    Artifact,
    Frame,
    HITLRequest,
    Project,
)
from app.settings import settings

router = Router(name="debug")


def _is_owner(chat_id: int) -> bool:
    return chat_id == settings.telegram_owner_chat_id


# ────────────────────────────── /debug router ───────────────────────────────


@router.message(Command("debug"))
async def cmd_debug(message: Message, command: CommandObject) -> None:
    """Главный диспетчер /debug подкоманд."""
    if not message.from_user or not _is_owner(message.from_user.id):
        await message.answer("⛔ Только владелец.")
        return

    args = (command.args or "").strip()
    if not args:
        await message.answer(
            "<b>🩺 /debug</b>\n\n"
            "Подкоманды:\n"
            "<code>/debug status</code> — активные проекты + статусы.\n"
            "<code>/debug project &lt;id&gt;</code> — детальный дамп проекта.\n"
            "<code>/debug locks</code> — зависшие / request_stop проекты.\n"
            "<code>/debug logs &lt;id&gt; [N]</code> — последние N строк лога.\n"
            "<code>/debug ai [session_id]</code> — список AI-сессий.\n"
            "<code>/debug selftest</code> — быстрый self-check.\n"
            "<code>/debug api</code> — статус локального API.",
            parse_mode="HTML",
        )
        return

    parts = args.split(maxsplit=2)
    sub = parts[0].lower()
    rest = parts[1:] if len(parts) > 1 else []

    handlers = {
        "status": _cmd_status,
        "project": _cmd_project,
        "locks": _cmd_locks,
        "logs": _cmd_logs,
        "ai": _cmd_ai_summary,
        "selftest": _cmd_selftest,
        "api": _cmd_api,
    }
    handler = handlers.get(sub)
    if handler is None:
        await message.answer(
            f"Неизвестная подкоманда: <code>{html.escape(sub)}</code>.\n"
            f"Доступные: {', '.join(handlers.keys())}",
            parse_mode="HTML",
        )
        return
    await handler(message, rest)


# ────────────────────────────── status ──────────────────────────────────────


async def _cmd_status(message: Message, rest: list[str]) -> None:
    """Сводка по проектам (топ 15 по updated_at)."""
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(Project).order_by(Project.id.desc()).limit(20)
            )
        ).scalars().all()

    if not rows:
        await message.answer("Проектов нет.")
        return

    lines = ["<b>📊 Последние 20 проектов:</b>", ""]
    for p in rows:
        slug = html.escape(p.slug or "?")[:40]
        status = html.escape(getattr(p.status, "value", str(p.status)))
        topic = html.escape((p.topic or "")[:50])
        batch = f" [batch={p.batch_id}]" if getattr(p, "batch_id", None) else ""
        lines.append(f"#{p.id:>3} <code>{status:<22}</code> {slug}{batch}\n     <i>{topic}</i>")

    text = "\n".join(lines)
    await message.answer(text[:4000], parse_mode="HTML")


# ────────────────────────────── project ─────────────────────────────────────


async def _cmd_project(message: Message, rest: list[str]) -> None:
    """Детальный дамп проекта."""
    if not rest:
        await message.answer("Нужен id. Пример: <code>/debug project 5</code>", parse_mode="HTML")
        return
    try:
        pid = int(rest[0])
    except ValueError:
        await message.answer("Невалидный id.")
        return

    async with session_scope() as db:
        stmt = (
            select(Project)
            .where(Project.id == pid)
            .options(
                selectinload(Project.frames),
                selectinload(Project.artifacts),
                selectinload(Project.hitl_requests),
            )
        )
        p = (await db.execute(stmt)).scalar_one_or_none()

    if p is None:
        await message.answer(f"Проект #{pid} не найден.")
        return

    frames = p.frames or []
    hitl = p.hitl_requests or []
    artifacts = p.artifacts or []

    # Сводка по кадрам по статусам
    frame_by_status: dict[str, int] = {}
    for f in frames:
        s = getattr(f.status, "value", str(f.status))
        frame_by_status[s] = frame_by_status.get(s, 0) + 1

    artifact_by_kind: dict[str, int] = {}
    for a in artifacts:
        k = getattr(a.kind, "value", str(a.kind))
        artifact_by_kind[k] = artifact_by_kind.get(k, 0) + 1

    pending_hitl = [h for h in hitl if getattr(h.decision, "value", str(h.decision)) == "pending"]

    lines = [
        f"<b>📂 Проект #{p.id}</b>",
        f"slug: <code>{html.escape(p.slug or '?')}</code>",
        f"status: <code>{html.escape(getattr(p.status, 'value', str(p.status)))}</code>",
        f"topic: <i>{html.escape((p.topic or '')[:200])}</i>",
        f"created: {p.created_at.isoformat() if p.created_at else '?'}",
        f"updated: {p.updated_at.isoformat() if p.updated_at else '?'}",
        "",
        f"<b>Кадры:</b> {len(frames)}",
    ]
    for s, c in sorted(frame_by_status.items()):
        lines.append(f"  • <code>{s}</code>: {c}")
    lines.extend([
        "",
        f"<b>Артефакты:</b> {len(artifacts)}",
    ])
    for k, c in sorted(artifact_by_kind.items()):
        lines.append(f"  • <code>{k}</code>: {c}")
    lines.extend([
        "",
        f"<b>HITL-запросы:</b> {len(hitl)} (pending: {len(pending_hitl)})",
    ])
    for h in pending_hitl[:5]:
        kind = getattr(h.kind, "value", str(h.kind))
        lines.append(f"  • #{h.id} kind={kind} frame={h.frame_id}")

    if hasattr(p, "batch_id") and p.batch_id:
        lines.append("")
        lines.append(f"<b>Batch:</b> #{p.batch_id}")

    await message.answer("\n".join(lines)[:4000], parse_mode="HTML")


# ────────────────────────────── locks ───────────────────────────────────────


async def _cmd_locks(message: Message, rest: list[str]) -> None:
    """Проекты с request_stop=True (просили остановить) или failed/zависшие."""
    async with session_scope() as db:
        all_p = (
            await db.execute(select(Project).order_by(Project.id.desc()).limit(50))
        ).scalars().all()

    stop_requested = [
        p for p in all_p if getattr(p, "request_stop", False)
    ]
    failed = [p for p in all_p if getattr(p.status, "value", str(p.status)) == "failed"]
    running = [
        p for p in all_p
        if getattr(p.status, "value", str(p.status)) in (
            "planning", "scripting", "splitting", "generating_hero",
            "generating_items", "generating_image_prompts", "generating_images",
            "generating_animation_prompts", "generating_videos",
            "generating_audio", "assembling", "publishing",
        )
        and not (
            getattr(p, "enrich_slots_count", None) and
            any(getattr(p.status, "value", "").startswith("enriching_") for _ in [1])
        )
    ]

    lines = ["<b>🔒 Locks / зависшие проекты:</b>", ""]
    lines.append(f"<b>request_stop=True</b> ({len(stop_requested)}):")
    for p in stop_requested[:10]:
        lines.append(
            f"  #{p.id} <code>{getattr(p.status, 'value', '?')}</code> "
            f"{html.escape((p.slug or '?')[:40])}"
        )
    lines.append("")
    lines.append(f"<b>failed</b> ({len(failed)}):")
    for p in failed[:10]:
        last_err = getattr(p, "last_error", None) or ""
        lines.append(
            f"  #{p.id} {html.escape((p.slug or '?')[:30])} "
            f"<i>{html.escape(last_err[:60])}</i>"
        )
    lines.append("")
    lines.append(f"<b>в работе (running)</b> ({len(running)}):")
    for p in running[:10]:
        upd = p.updated_at.strftime("%H:%M") if p.updated_at else "?"
        lines.append(
            f"  #{p.id} <code>{getattr(p.status, 'value', '?')}</code> "
            f"upd={upd}"
        )

    await message.answer("\n".join(lines)[:4000], parse_mode="HTML")


# ────────────────────────────── logs ────────────────────────────────────────


async def _cmd_logs(message: Message, rest: list[str]) -> None:
    """Последние N строк из лога с упоминанием project_id (если есть log-файл).

    Лог-файла как такового нет (loguru пишет в stderr/stdout), но если в
    settings.log_file задан путь — читаем оттуда.
    """
    if not rest:
        await message.answer(
            "Нужен id. Пример: <code>/debug logs 5 100</code>", parse_mode="HTML"
        )
        return
    try:
        pid = int(rest[0])
    except ValueError:
        await message.answer("Невалидный id.")
        return

    n = 50
    if len(rest) > 1:
        try:
            n = max(1, min(int(rest[1]), 500))
        except ValueError:
            pass

    log_file = os.environ.get("LOG_FILE") or ""
    if not log_file:
        await message.answer(
            "Файла лога нет (loguru пишет в stderr). "
            "Чтобы включить — задай в .env <code>LOG_FILE=./data/app.log</code> "
            "и добавь loguru sink.",
            parse_mode="HTML",
        )
        return

    log_path = Path(log_file)
    if not log_path.exists():
        await message.answer(f"Файл {log_file} не существует.")
        return

    needle = f"#{pid}"
    matched: list[str] = []
    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if needle in line:
                    matched.append(line.rstrip())
                    if len(matched) > n * 3:
                        matched = matched[-n:]
    except Exception as e:  # noqa: BLE001
        await message.answer(f"Ошибка чтения лога: {html.escape(str(e))}", parse_mode="HTML")
        return

    if not matched:
        await message.answer(f"Нет строк с <code>#{pid}</code>.", parse_mode="HTML")
        return

    snippet = "\n".join(matched[-n:])
    if len(snippet) > 3500:
        snippet = snippet[-3500:]
    await message.answer(
        f"<b>📜 Лог #{pid} (последние {min(n, len(matched))} строк):</b>\n"
        f"<pre>{html.escape(snippet)}</pre>",
        parse_mode="HTML",
    )


# ────────────────────────────── ai summary ──────────────────────────────────


async def _cmd_ai_summary(message: Message, rest: list[str]) -> None:
    """Список AI-сессий или дамп одной."""
    if rest:
        # /debug ai <id>
        try:
            sid = int(rest[0])
        except ValueError:
            await message.answer("Невалидный session_id.")
            return
        async with session_scope() as db:
            stmt = (
                select(AISession)
                .where(AISession.id == sid)
                .options(
                    selectinload(AISession.messages),
                    selectinload(AISession.tool_calls),
                )
            )
            s = (await db.execute(stmt)).scalar_one_or_none()
        if s is None:
            await message.answer(f"AI-сессия #{sid} не найдена.")
            return

        msgs = s.messages or []
        tcs = s.tool_calls or []
        lines = [
            f"<b>🤖 AI-сессия #{s.id}</b>",
            f"<code>{s.status.value}</code> · <code>{s.mode.value}</code> · <code>{s.model}</code>",
            f"Шагов: {s.step_count}, токены: {s.total_tokens_in}+{s.total_tokens_out} (~{s.cost_rub:.2f}₽)",
            f"Сообщений: {len(msgs)}, tool_calls: {len(tcs)}",
            "",
            f"<i>Запрос:</i> {html.escape((s.initial_query or '')[:200])}",
        ]
        if s.final_answer:
            lines.append("")
            lines.append(f"<i>Итог:</i>\n<pre>{html.escape((s.final_answer or '')[:1500])}</pre>")
        await message.answer("\n".join(lines)[:4000], parse_mode="HTML")
        return

    # /debug ai — список последних 10 сессий
    async with session_scope() as db:
        rows = (
            await db.execute(
                select(AISession).order_by(AISession.id.desc()).limit(10)
            )
        ).scalars().all()

    if not rows:
        await message.answer("AI-сессий ещё нет.")
        return

    lines = ["<b>🤖 Последние 10 AI-сессий:</b>", ""]
    for s in rows:
        cost = f"{s.cost_rub:.2f}₽" if s.cost_rub else "—"
        lines.append(
            f"#{s.id:>3} <code>{s.status.value:<10}</code> {s.model:<22} "
            f"steps={s.step_count:>2} {cost:>7}"
        )
        lines.append(f"     <i>{html.escape((s.initial_query or '')[:80])}</i>")
    await message.answer("\n".join(lines)[:4000], parse_mode="HTML")


# ────────────────────────────── selftest ────────────────────────────────────


async def _cmd_selftest(message: Message, rest: list[str]) -> None:
    """Быстрый self-check всей подсистемы."""
    checks: list[tuple[str, str, str]] = []  # (name, status, details)

    # 1. SQLite WAL
    try:
        db_path = Path(settings.sqlite_path).resolve()
        if db_path.exists():
            with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
                jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
                bt = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            checks.append(("SQLite", "✅", f"journal={jm} busy_timeout={bt}ms"))
        else:
            checks.append(("SQLite", "⚠️", f"db not found at {db_path}"))
    except Exception as e:  # noqa: BLE001
        checks.append(("SQLite", "❌", str(e)[:80]))

    # 2. FFmpeg
    try:
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            first_line = out.decode("utf-8", errors="replace").split("\n", 1)[0]
            checks.append(("FFmpeg", "✅", first_line[:80]))
        else:
            checks.append(("FFmpeg", "❌", "не в PATH"))
    except Exception as e:  # noqa: BLE001
        checks.append(("FFmpeg", "❌", str(e)[:80]))

    # 3. CDP Chrome
    try:
        cdp_url = os.environ.get("BROWSER_CDP_URL", "http://localhost:29229")
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(f"{cdp_url}/json/version") as resp:
                if resp.status == 200:
                    data = await resp.json()
                    browser = data.get("Browser", "?")
                    checks.append(("CDP Chrome", "✅", f"{cdp_url} ({browser})"))
                else:
                    checks.append(("CDP Chrome", "⚠️", f"HTTP {resp.status}"))
    except Exception as e:  # noqa: BLE001
        checks.append(("CDP Chrome", "❌", f"{type(e).__name__}: {str(e)[:60]}"))

    # 4. AI агент (баланс)
    try:
        from app.ai_agent import get_config
        from app.ai_agent.client import AIClient

        cfg = get_config()
        if cfg.is_configured:
            client = AIClient(cfg)
            balance = await client.check_balance()
            if balance is not None:
                checks.append(("AI-агент", "✅", f"{cfg.default_model}, баланс {balance:.2f}₽"))
            else:
                checks.append(("AI-агент", "⚠️", "ключ задан, баланс не получен"))
        else:
            checks.append(("AI-агент", "⚠️", "ORCHESTRATOR_AI_API_KEY не задан"))
    except Exception as e:  # noqa: BLE001
        checks.append(("AI-агент", "❌", str(e)[:80]))

    # 5. Orchestrator API (если поднят)
    try:
        api_url = "http://127.0.0.1:8787"
        timeout = aiohttp.ClientTimeout(total=2)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            async with sess.get(f"{api_url}/health") as resp:
                if resp.status == 200:
                    checks.append(("Orchestrator API", "✅", f"{api_url}/health 200"))
                else:
                    checks.append(("Orchestrator API", "⚠️", f"HTTP {resp.status}"))
    except Exception as e:  # noqa: BLE001
        checks.append(("Orchestrator API", "⚠️", f"не поднят ({type(e).__name__})"))

    # 6. Disk space
    try:
        data_dir = Path(settings.data_dir).resolve()
        if data_dir.exists():
            stat = shutil.disk_usage(data_dir)
            free_gb = stat.free / 1024**3
            total_gb = stat.total / 1024**3
            emoji = "✅" if free_gb > 5 else "⚠️" if free_gb > 1 else "❌"
            checks.append(("Disk space", emoji, f"{free_gb:.1f}GB free / {total_gb:.0f}GB total"))
        else:
            checks.append(("Disk space", "⚠️", f"data dir not found: {data_dir}"))
    except Exception as e:  # noqa: BLE001
        checks.append(("Disk space", "❌", str(e)[:80]))

    lines = ["<b>🩺 Self-test результат:</b>", ""]
    for name, status, details in checks:
        lines.append(f"{status} <b>{name}</b>: <code>{html.escape(details)}</code>")
    failed = [c for c in checks if c[1] == "❌"]
    warn = [c for c in checks if c[1] == "⚠️"]
    lines.append("")
    if not failed and not warn:
        lines.append("<b>Всё ок ✅</b>")
    else:
        lines.append(f"<b>Ошибок:</b> {len(failed)} ❌, <b>warnings:</b> {len(warn)} ⚠️")

    await message.answer("\n".join(lines)[:4000], parse_mode="HTML")


# ────────────────────────────── api ─────────────────────────────────────────


async def _cmd_api(message: Message, rest: list[str]) -> None:
    """Статус локального orchestrator_api (FastAPI на 127.0.0.1:8787)."""
    api_url = "http://127.0.0.1:8787"
    timeout = aiohttp.ClientTimeout(total=3)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            # /health
            async with sess.get(f"{api_url}/health") as resp:
                health_status = resp.status
                health_body = (await resp.text())[:200]
            # /batches
            async with sess.get(f"{api_url}/batches") as resp:
                batches_status = resp.status
                batches_body = await resp.json() if resp.status == 200 else None
    except Exception as e:  # noqa: BLE001
        await message.answer(
            f"❌ <code>{api_url}</code> недоступен:\n"
            f"<code>{html.escape(type(e).__name__ + ': ' + str(e)[:100])}</code>\n\n"
            f"Запусти из workspace:\n"
            f"<code>uvicorn app.orchestrator_api:app --host 127.0.0.1 --port 8787</code>",
            parse_mode="HTML",
        )
        return

    batches_count = len(batches_body.get("batches", [])) if isinstance(batches_body, dict) else "?"

    await message.answer(
        f"<b>🌐 Orchestrator API:</b>\n\n"
        f"<code>{api_url}</code>\n"
        f"<b>/health:</b> HTTP {health_status} — <code>{html.escape(health_body)}</code>\n"
        f"<b>/batches:</b> HTTP {batches_status} — <code>{batches_count} batches</code>",
        parse_mode="HTML",
    )
