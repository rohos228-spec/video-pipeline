"""Telegram-бот: приём команд (/new, /status, /pause, /resume, /abort),
отправка HITL-запросов на подтверждение промежуточных артефактов."""

from __future__ import annotations

import re
from typing import Any

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message
from loguru import logger
from sqlalchemy import select

from app.db import session_scope
from app.models import HITLDecision, HITLRequest, Project, ProjectStatus
from app.settings import settings

dp = Dispatcher()


def is_owner(msg: Message) -> bool:
    return msg.from_user is not None and msg.from_user.id == settings.telegram_owner_chat_id


def _parse_new_command(text: str) -> tuple[str, str]:
    """Парсит `/new <тема> [--hero|--no-hero|--auto]` → (topic, hero_mode)."""
    body = text.removeprefix("/new").strip()
    hero_mode = "auto"
    m = re.search(r"(--hero|--no-hero|--auto)\b", body)
    if m:
        flag = m.group(1)
        hero_mode = {"--hero": "hero", "--no-hero": "no_hero", "--auto": "auto"}[flag]
        body = (body[: m.start()] + body[m.end() :]).strip()
    return body, hero_mode


@dp.message(CommandStart())
async def cmd_start(msg: Message) -> None:
    if not is_owner(msg):
        return
    await msg.answer(
        "Готов к работе. Команды:\n"
        "  /new <тема> [--hero|--no-hero|--auto] — запустить новый ролик\n"
        "  /status — список активных проектов\n"
        "  /status <id> — статус конкретного проекта\n"
        "  /pause <id> | /resume <id> | /abort <id>"
    )


@dp.message(Command("new"))
async def cmd_new(msg: Message) -> None:
    if not is_owner(msg):
        return
    topic, hero_mode = _parse_new_command(msg.text or "")
    if not topic:
        await msg.answer("Использование: /new <тема> [--hero|--no-hero|--auto]")
        return

    slug_base = re.sub(r"[^a-zа-я0-9]+", "-", topic.lower(), flags=re.IGNORECASE).strip("-")[:40] or "ролик"
    async with session_scope() as s:
        # гарантируем уникальный slug
        i = 1
        slug = slug_base
        while (await s.execute(select(Project).where(Project.slug == slug))).scalar_one_or_none():
            i += 1
            slug = f"{slug_base}-{i}"
        project = Project(slug=slug, topic=topic, hero_mode=hero_mode, status=ProjectStatus.planning)
        s.add(project)
        await s.flush()
        project_id = project.id
    await msg.answer(
        f"Проект создан: #{project_id} `{slug}`\nТема: {topic}\nРежим героя: {hero_mode}\n\n"
        "Дальше бот сам проведёт по этапам, буду присылать промежуточные результаты.",
        parse_mode="Markdown",
    )
    logger.info("new project {} '{}' hero={}", project_id, slug, hero_mode)


@dp.message(Command("status"))
async def cmd_status(msg: Message) -> None:
    if not is_owner(msg):
        return
    parts = (msg.text or "").split()
    async with session_scope() as s:
        if len(parts) >= 2 and parts[1].isdigit():
            pid = int(parts[1])
            project = (
                await s.execute(select(Project).where(Project.id == pid))
            ).scalar_one_or_none()
            if project is None:
                await msg.answer(f"Проект #{pid} не найден")
                return
            await msg.answer(
                f"#{project.id} `{project.slug}`\n"
                f"тема: {project.topic}\n"
                f"статус: {project.status.value}\n"
                f"обновлён: {project.updated_at:%Y-%m-%d %H:%M}",
                parse_mode="Markdown",
            )
        else:
            rows = (await s.execute(select(Project).order_by(Project.id.desc()).limit(20))).scalars().all()
            if not rows:
                await msg.answer("Пока нет проектов.")
                return
            lines = [f"#{p.id} `{p.slug}` — {p.status.value}" for p in rows]
            await msg.answer("Последние проекты:\n" + "\n".join(lines), parse_mode="Markdown")


@dp.callback_query(F.data.startswith("hitl:"))
async def on_hitl_callback(cb: CallbackQuery) -> None:
    if cb.from_user.id != settings.telegram_owner_chat_id:
        await cb.answer("Нет доступа", show_alert=True)
        return
    # формат callback_data: hitl:<hitl_id>:<action>
    try:
        _, hitl_id_s, action = (cb.data or "").split(":", 2)
        hitl_id = int(hitl_id_s)
    except Exception:
        await cb.answer("Плохой callback", show_alert=True)
        return
    async with session_scope() as s:
        req = (
            await s.execute(select(HITLRequest).where(HITLRequest.id == hitl_id))
        ).scalar_one_or_none()
        if req is None:
            await cb.answer("HITL-запрос не найден", show_alert=True)
            return
        if req.decision is not HITLDecision.pending:
            await cb.answer(f"Уже обработан: {req.decision.value}", show_alert=True)
            return
        req.decision = {
            "approve": HITLDecision.approved,
            "regen": HITLDecision.regenerate,
            "reject": HITLDecision.rejected,
        }.get(action, HITLDecision.pending)
    await cb.answer(f"Решение: {action}")


async def build_bot() -> tuple[Bot, Dispatcher]:
    bot = Bot(settings.telegram_bot_token)
    return bot, dp


async def notify_owner(bot: Bot, text: str, **kwargs: Any) -> Message:
    return await bot.send_message(settings.telegram_owner_chat_id, text, **kwargs)
