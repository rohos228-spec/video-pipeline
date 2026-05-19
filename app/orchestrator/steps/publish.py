"""Шаг 12: публикация готового ролика на 5 площадок через MoreLogin-профиль."""

from __future__ import annotations

from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.bots.publishers import publish_everywhere
from app.models import Artifact, ArtifactKind, Project, ProjectStatus
from app.settings import settings

_META_KEY = "published_platforms"


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.publishing:
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
    # Уже опубликованные платформы пропускаем, чтобы при повторе не было дубликатов.
    meta = project.meta or {}
    already: dict[str, str] = meta.get(_META_KEY, {})
    skip_platforms = set(already.keys())
    logger.info(
        "[#{}] publishing {} (skip={})", project.id, final.path, sorted(skip_platforms) or "—"
    )

    results = await publish_everywhere(Path(final.path), caption, skip_platforms=skip_platforms)

    # Запомним, что успели выложить, чтобы ретрай не дублировал.
    for r in results:
        if r.ok:
            already[r.platform] = r.url or ""
    meta[_META_KEY] = already
    project.meta = meta
    flag_modified(project, "meta")

    ok = [r for r in results if r.ok]
    fails = [r for r in results if not r.ok]
    lines: list[str] = [f"Публикация проекта #{project.id}:"]
    for r in results:
        if r.ok:
            lines.append(f"- {r.platform}: OK {r.url or ''}")
        else:
            lines.append(f"- {r.platform}: FAIL — {r.error or ''}")
    if skip_platforms:
        lines.append(f"(пропущены уже опубликованные: {', '.join(sorted(skip_platforms))})")
    await bot.send_message(settings.telegram_owner_chat_id, "\n".join(lines))

    total_platforms = 5  # см. ALL_PUBLISHERS
    published_count = len(already)
    if published_count >= total_platforms:
        project.status = ProjectStatus.published
        logger.info("[#{}] publish done, все {} платформ", project.id, total_platforms)
    else:
        # Остаёмся в assembled — воркер повторит позже, но будет пропускать уже опубликованные.
        logger.warning(
            "[#{}] {}/{} платформ опубликовано ({} ok в этот раз, {} fail), останемся в assembled",
            project.id, published_count, total_platforms, len(ok), len(fails),
        )
    await session.flush()
