"""Один проект по умолчанию при первом запуске (веб-студия без Telegram)."""

from __future__ import annotations

from loguru import logger
from sqlalchemy import func, select

from app.db import session_scope
from app.models import Project
from app.seed_pilot import DEFAULT_HERO_MODE, DEFAULT_TOPIC, seed
from app.settings import settings


async def ensure_default_project() -> int | None:
    """Создать пилотный проект, если в БД ещё нет ни одного."""
    async with session_scope() as s:
        count = (
            await s.execute(select(func.count()).select_from(Project))
        ).scalar_one()
        if count and int(count) > 0:
            return None

    auto = not settings.telegram_active
    pid = await seed(topic=DEFAULT_TOPIC, hero_mode=DEFAULT_HERO_MODE)
    if auto and pid:
        async with session_scope() as s:
            p = await s.get(Project, pid)
            if p is not None:
                p.auto_mode = True
                meta = dict(p.meta or {})
                meta.setdefault("graph_executor", True)
                p.meta = meta
                await s.flush()
        logger.info(
            "default project #{} created (auto_mode=True, web-only)",
            pid,
        )
    else:
        logger.info("default project #{} created", pid)
    return pid


def default_auto_mode_for_new_project() -> bool:
    """Новые проекты в web-only режиме идут с авто-продвижением."""
    if settings.hitl_auto_approve:
        return True
    return not settings.telegram_active
