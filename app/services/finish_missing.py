"""Доделка: найти кадры без файла на диске и поставить в очередь генерации."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.models import Frame, Project, ProjectStatus
from app.services.animation_prompt_gpt import (
    scan_missing_animation_prompts,
    sync_animation_prompts_from_xlsx,
)
from app.services.scan_frames import (
    reset_frames_for_video_regen,
    reset_frames_to_image_prompt_ready,
    scan_missing_frames,
    scan_missing_videos,
    sync_frames_with_disk_images,
)
from app.services.step_cancel import clear_stop


async def trigger_finish_missing_images(
    session: AsyncSession, project: Project
) -> dict:
    missing = await scan_missing_frames(session, project)
    if not missing:
        return {
            "ok": True,
            "kind": "images",
            "missing": [],
            "queued": 0,
            "already_running": project.status is ProjectStatus.generating_images,
            "message": "Все кадры с image_prompt уже имеют frame_NNN_*.png в scenes/",
        }
    already = project.status is ProjectStatus.generating_images
    synced = await sync_frames_with_disk_images(session, project)
    queued = await reset_frames_to_image_prompt_ready(session, project, missing)
    if not already:
        project.status = ProjectStatus.generating_images
    head = ", ".join(str(n) for n in missing[:30])
    if len(missing) > 30:
        head += f", … +{len(missing) - 30}"
    msg = (
        f"В очередь image_prompt_ready: {queued} кадров ({head})"
        if queued
        else "Нечего ставить в очередь"
    )
    if already:
        msg = f"Шаг картинок уже идёт. {msg}"
    return {
        "ok": True,
        "kind": "images",
        "missing": missing,
        "queued": queued,
        "synced_on_disk": synced,
        "already_running": already,
        "message": msg,
    }


async def trigger_resume_animation_prompts(
    session: AsyncSession, project: Project
) -> dict:
    """Догонка anim_pr: R48 xlsx → БД, затем generating_animation_prompts."""
    synced = await sync_animation_prompts_from_xlsx(session, project)
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
        )
    ).scalars().all()
    missing = scan_missing_animation_prompts(project, frames)
    already_done = sum(1 for fr in frames if (fr.animation_prompt or "").strip())
    if not missing:
        project.status = ProjectStatus.animation_prompts_ready
        meta = dict(project.meta or {})
        meta.pop("user_stop", None)
        project.meta = meta
        clear_stop(project.id)
        return {
            "ok": True,
            "kind": "animation_prompts",
            "missing": [],
            "synced_from_xlsx": synced,
            "already_done": already_done,
            "queued": 0,
            "already_running": False,
            "message": (
                f"Все {already_done} кадров с картинкой уже имеют animation_prompt "
                "(plan R48 / БД)"
            ),
        }
    already = project.status is ProjectStatus.generating_animation_prompts
    clear_stop(project.id)
    meta = dict(project.meta or {})
    meta.pop("user_stop", None)
    project.meta = meta
    if not already:
        project.status = ProjectStatus.generating_animation_prompts
    head = ", ".join(str(n) for n in missing[:30])
    if len(missing) > 30:
        head += f", … +{len(missing) - 30}"
    msg = (
        f"Догонка anim_pr: готово {already_done}, осталось {len(missing)} ({head})"
    )
    if already:
        msg = f"Шаг anim_pr уже идёт. {msg}"
    return {
        "ok": True,
        "kind": "animation_prompts",
        "missing": missing,
        "synced_from_xlsx": synced,
        "already_done": already_done,
        "queued": len(missing),
        "already_running": already,
        "message": msg,
    }


async def trigger_finish_missing_videos(
    session: AsyncSession, project: Project
) -> dict:
    missing = await scan_missing_videos(session, project)
    if not missing:
        return {
            "ok": True,
            "kind": "videos",
            "missing": [],
            "queued": 0,
            "already_running": project.status is ProjectStatus.generating_videos,
            "message": (
                "Все кадры с animation_prompt и картинкой уже имеют "
                "clip_NNN_*.mp4 в videos/"
            ),
        }
    already = project.status is ProjectStatus.generating_videos
    queued = await reset_frames_for_video_regen(session, project, missing)
    if not already:
        project.status = ProjectStatus.generating_videos
    head = ", ".join(str(n) for n in missing[:30])
    if len(missing) > 30:
        head += f", … +{len(missing) - 30}"
    msg = (
        f"В очередь догенерации видео: {queued} кадров ({head})"
        if queued
        else "Нечего ставить в очередь"
    )
    if already:
        msg = f"Шаг видео уже идёт. {msg}"
    return {
        "ok": True,
        "kind": "videos",
        "missing": missing,
        "queued": queued,
        "already_running": already,
        "message": msg,
    }
