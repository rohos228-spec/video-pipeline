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
_UNCONFIRMED_KEY = "publish_unconfirmed"


async def preview_publication(session: AsyncSession, project: Project) -> dict:
    """Read-only validation of the existing publish job. Never opens a browser."""
    from app.bots.publishers import ALL_PUBLISHERS
    from app.services.artifact_verifier import verify_artifact

    already = (project.meta or {}).get(_META_KEY, {})
    already = already if isinstance(already, dict) else {}
    result = {"dry_run":True, "send_allowed":False, "requires_approval":True,
              "project_status":project.status.value, "artifact_uuid":None,
              "caption_characters":len((project.topic or project.slug)[:2200]),
              "platforms":[{"platform":cls.platform, "already_recorded":cls.platform in already}
                           for cls in ALL_PUBLISHERS]}
    with session.no_autoflush:
        final = await session.scalar(select(Artifact).where(
            Artifact.project_id==project.id, Artifact.kind==ArtifactKind.final_video
        ).order_by(Artifact.id.desc()).limit(1))
    if final is None:
        result["validation"] = {"status":"failed", "reason":"final_artifact_missing"}
        return result
    try:
        result["validation"] = await verify_artifact(final.path, root=project.data_dir, kind="assembly")
    except ValueError:
        result["validation"] = {"status":"failed", "reason":"artifact_outside_project"}
        return result
    except OSError:
        result["validation"] = {"status":"unknown", "reason":"artifact_unreadable"}
        return result
    result["artifact_uuid"] = final.uuid
    return result


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    from app.services.actions_v1 import get_permission
    from app.services.observation import _attempt
    from app.services.media_jobs import MediaPolicyError
    ctx = _attempt.get()
    if get_permission(project.id) is not None or (ctx and ctx.scope.mode == "managed"):
        raise MediaPolicyError("managed publication is disabled; use read-only preview")
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
    # Неподтверждённые платформы человек проверяет вручную: автоповтор мог бы задвоить пост.
    unconfirmed: dict[str, str] = dict(meta.get(_UNCONFIRMED_KEY) or {})
    skip_platforms = set(already.keys()) | set(unconfirmed.keys())
    logger.info(
        "[#{}] publishing {} (skip={})", project.id, final.path, sorted(skip_platforms) or "—"
    )

    from app.bots.publishers import ALL_PUBLISHERS
    if all(cls.platform in skip_platforms for cls in ALL_PUBLISHERS):
        logger.warning("[#{}] publish: нечего публиковать, неподтверждённые платформы {} ждут человека",
                       project.id, sorted(unconfirmed))
        return

    results = await publish_everywhere(Path(final.path), caption, skip_platforms=skip_platforms)

    # Успехом записываем только подтверждённое; неподтверждённое помечаем отдельно.
    for r in results:
        if r.ok:
            already[r.platform] = r.url or ""
        elif r.status == "unconfirmed":
            unconfirmed[r.platform] = r.url or ""
    meta[_META_KEY] = already
    meta[_UNCONFIRMED_KEY] = unconfirmed
    project.meta = meta
    flag_modified(project, "meta")

    ok = [r for r in results if r.ok]
    fails = [r for r in results if not r.ok]
    lines: list[str] = [f"Публикация проекта #{project.id}:"]
    for r in results:
        if r.ok:
            lines.append(f"- {r.platform}: OK {r.url or ''}")
        elif r.status == "unconfirmed":
            lines.append(f"- {r.platform}: НЕ ПОДТВЕРЖДЕНО — проверьте вручную {r.url or ''}")
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
