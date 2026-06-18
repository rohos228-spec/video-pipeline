"""Доделка: найти кадры без файла на диске и поставить в очередь генерации."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.models import Frame, Project, ProjectStatus
from app.services.animation_prompt_gpt import (
    scan_missing_animation_prompts,
    scan_missing_animation_prompts_shot2,
    sync_animation_prompts_from_xlsx,
)
from app.services.scan_frames import (
    reset_frames_for_video_regen,
    reset_frames_to_image_prompt_ready,
    reset_shot2_for_video_regen,
    reset_shot2_to_prompt_ready,
    scan_missing_frames,
    scan_missing_shot2_frames,
    scan_missing_shot2_videos,
    scan_missing_videos_shot1,
    sync_frames_with_disk_images,
)
from app.services.step_cancel import clear_stop


def _wake_worker_for_finish(project: Project, running: ProjectStatus) -> bool:
    """Доделка = явный ручной перезапуск: снять sleep/stop после ошибок шага."""
    from loguru import logger

    from app.services.step_failure_policy import clear_failure_backoff_for_manual_start

    clear_stop(project.id)
    cleared = clear_failure_backoff_for_manual_start(
        project, running_key=running.value
    )
    if cleared:
        logger.info(
            "[#{}] finish_missing {}: снята пауза после ошибок",
            project.id,
            running.value,
        )
    return cleared


async def trigger_finish_missing_images(
    session: AsyncSession, project: Project
) -> dict:
    missing_shot1 = await scan_missing_frames(session, project)
    missing_shot2 = await scan_missing_shot2_frames(session, project)
    if not missing_shot1 and not missing_shot2:
        return {
            "ok": True,
            "kind": "images",
            "missing": [],
            "missing_shot1": [],
            "missing_shot2": [],
            "queued": 0,
            "queued_shot1": 0,
            "queued_shot2": 0,
            "already_running": project.status is ProjectStatus.generating_images,
            "message": (
                "Все кадры shot_01 и shot_02 (где есть промт) уже на диске в scenes/"
            ),
        }
    already = project.status is ProjectStatus.generating_images
    synced = await sync_frames_with_disk_images(session, project)
    queued_shot1 = await reset_frames_to_image_prompt_ready(
        session, project, missing_shot1
    )
    queued_shot2 = await reset_shot2_to_prompt_ready(
        session, project, missing_shot2
    )
    queued = queued_shot1 + queued_shot2
    if not already and queued:
        project.status = ProjectStatus.generating_images
    _wake_worker_for_finish(project, ProjectStatus.generating_images)
    parts: list[str] = []
    if missing_shot1:
        head1 = ", ".join(str(n) for n in missing_shot1[:20])
        if len(missing_shot1) > 20:
            head1 += f", … +{len(missing_shot1) - 20}"
        parts.append(f"shot_01: {queued_shot1} ({head1})")
    if missing_shot2:
        head2 = ", ".join(str(n) for n in missing_shot2[:20])
        if len(missing_shot2) > 20:
            head2 += f", … +{len(missing_shot2) - 20}"
        parts.append(f"shot_02: {queued_shot2} ({head2})")
    msg = "В очередь: " + "; ".join(parts) if parts else "Нечего ставить в очередь"
    if already:
        msg = f"Шаг картинок уже идёт. {msg}"
    missing = sorted(set(missing_shot1) | set(missing_shot2))
    return {
        "ok": True,
        "kind": "images",
        "missing": missing,
        "missing_shot1": missing_shot1,
        "missing_shot2": missing_shot2,
        "queued": queued,
        "queued_shot1": queued_shot1,
        "queued_shot2": queued_shot2,
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
    missing_shot1 = scan_missing_animation_prompts(project, frames)
    missing_shot2 = scan_missing_animation_prompts_shot2(project, frames)
    already_done = sum(1 for fr in frames if (fr.animation_prompt or "").strip())
    if not missing_shot1 and not missing_shot2:
        project.status = ProjectStatus.animation_prompts_ready
        meta = dict(project.meta or {})
        meta.pop("user_stop", None)
        project.meta = meta
        clear_stop(project.id)
        return {
            "ok": True,
            "kind": "animation_prompts",
            "missing": [],
            "missing_shot1": [],
            "missing_shot2": [],
            "synced_from_xlsx": synced,
            "already_done": already_done,
            "queued": 0,
            "already_running": False,
            "message": (
                "Все промты анимации shot_01 и shot_02 (где есть картинки) "
                "уже в plan R48/R64 или БД"
            ),
        }
    already = project.status is ProjectStatus.generating_animation_prompts
    _wake_worker_for_finish(project, ProjectStatus.generating_animation_prompts)
    meta = dict(project.meta or {})
    meta.pop("user_stop", None)
    project.meta = meta
    if not already:
        project.status = ProjectStatus.generating_animation_prompts
    parts: list[str] = []
    if missing_shot1:
        head1 = ", ".join(str(n) for n in missing_shot1[:20])
        if len(missing_shot1) > 20:
            head1 += f", … +{len(missing_shot1) - 20}"
        parts.append(f"shot_01: {len(missing_shot1)} ({head1})")
    if missing_shot2:
        head2 = ", ".join(str(n) for n in missing_shot2[:20])
        if len(missing_shot2) > 20:
            head2 += f", … +{len(missing_shot2) - 20}"
        parts.append(f"shot_02: {len(missing_shot2)} ({head2})")
    msg = "Догонка anim_pr: " + "; ".join(parts) if parts else "Нечего догонять"
    if already:
        msg = f"Шаг anim_pr уже идёт. {msg}"
    missing = sorted(set(missing_shot1) | set(missing_shot2))
    return {
        "ok": True,
        "kind": "animation_prompts",
        "missing": missing,
        "missing_shot1": missing_shot1,
        "missing_shot2": missing_shot2,
        "synced_from_xlsx": synced,
        "already_done": already_done,
        "queued": len(missing),
        "already_running": already,
        "message": msg,
    }


async def trigger_finish_missing_videos(
    session: AsyncSession, project: Project
) -> dict:
    missing_shot1 = await scan_missing_videos_shot1(session, project)
    missing_shot2 = await scan_missing_shot2_videos(session, project)
    if not missing_shot1 and not missing_shot2:
        return {
            "ok": True,
            "kind": "videos",
            "missing": [],
            "missing_shot1": [],
            "missing_shot2": [],
            "queued": 0,
            "queued_shot1": 0,
            "queued_shot2": 0,
            "already_running": project.status is ProjectStatus.generating_videos,
            "message": (
                "Все clip shot_01 и shot_02 (где есть промты и картинки) "
                "уже на диске в videos/"
            ),
        }
    already = project.status is ProjectStatus.generating_videos
    queued_shot1 = await reset_frames_for_video_regen(
        session, project, missing_shot1
    )
    queued_shot2 = await reset_shot2_for_video_regen(
        session, project, missing_shot2
    )
    queued = queued_shot1 + queued_shot2
    if not already and queued:
        project.status = ProjectStatus.generating_videos
    _wake_worker_for_finish(project, ProjectStatus.generating_videos)
    parts: list[str] = []
    if missing_shot1:
        head1 = ", ".join(str(n) for n in missing_shot1[:20])
        if len(missing_shot1) > 20:
            head1 += f", … +{len(missing_shot1) - 20}"
        parts.append(f"shot_01: {queued_shot1} ({head1})")
    if missing_shot2:
        head2 = ", ".join(str(n) for n in missing_shot2[:20])
        if len(missing_shot2) > 20:
            head2 += f", … +{len(missing_shot2) - 20}"
        parts.append(f"shot_02: {queued_shot2} ({head2})")
    msg = "В очередь видео: " + "; ".join(parts) if parts else "Нечего ставить в очередь"
    if already:
        msg = f"Шаг видео уже идёт. {msg}"
    missing = sorted(set(missing_shot1) | set(missing_shot2))
    return {
        "ok": True,
        "kind": "videos",
        "missing": missing,
        "missing_shot1": missing_shot1,
        "missing_shot2": missing_shot2,
        "queued": queued,
        "queued_shot1": queued_shot1,
        "queued_shot2": queued_shot2,
        "already_running": already,
        "message": msg,
    }
