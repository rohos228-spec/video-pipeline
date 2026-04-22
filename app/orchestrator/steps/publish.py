"""Шаг 12: публикация готового ролика на 5 площадок через MoreLogin-профиль."""

from __future__ import annotations

from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.publishers import publish_everywhere
from app.models import Artifact, ArtifactKind, Project, ProjectStatus
from app.services.hitl import send_hitl_text  # noqa: F401 (оставим на будущее)
from app.settings import settings


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.assembled:
        return
    if not settings.social_publish_enabled:
        logger.info("[#{}] publish отключён (SOCIAL_PUBLISH_ENABLED=false)", project.id)
        project.status = ProjectStatus.published
        await session.flush()
        return

    final = (
        await session.execute(
            select(Artifact)
            .where(Artifact.project_id == project.id, Artifact.kind == ArtifactKind.final_video)
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if final is None:
        raise RuntimeError("нет финального видео для публикации")

    caption = (project.topic or project.slug)[:2200]
    logger.info("[#{}] publishing {} → 5 platforms", project.id, final.path)

    results = await publish_everywhere(Path(final.path), caption)
    ok = [r for r in results if r.ok]
    fails = [r for r in results if not r.ok]
    status_msg = (
        f"Публикация проекта #{project.id}:\n"
        + "\n".join(
            f"- {r.platform}: " + ("OK " + (r.url or "")) if r.ok else f"- {r.platform}: FAIL — {r.error or ''}"
            for r in results
        )
    )
    await bot.send_message(settings.telegram_owner_chat_id, status_msg)

    project.status = ProjectStatus.published if not fails else ProjectStatus.assembled
    if fails:
        logger.warning("[#{}] {} платформ с ошибками — остались в assembled для повтора",
                       project.id, len(fails))
    await session.flush()
    logger.info("[#{}] publish done, {} ok / {} fail", project.id, len(ok), len(fails))
