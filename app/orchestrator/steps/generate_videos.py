"""Шаг 9: для каждого кадра — сгенерировать 8-сек клип в outsee veo-3-fast
Relax, используя картинку кадра как стартовый кадр. В конце — HITL approve_videos.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.bots.outsee import OutseeBot
from app.generation_options import (
    ASPECT_RATIOS_BY_ID,
    DEFAULTS,
    VIDEO_GENERATORS_BY_ID,
    VIDEO_RESOLUTIONS_BY_ID,
    build_gen_id_prefix,
)
from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    FrameStatus,
    HITLKind,
    Project,
    ProjectStatus,
)
from app.services.hitl import send_hitl_text
from app.services.artifact_recovery import (
    recover_scene_images_from_disk,
    recover_scene_videos_from_disk,
)
from app.services.scan_frames import is_valid_scene_image, newest_frame_image_path
from app.services.animation_prompt_gpt import animation_prompt_shot2_in_plan_xlsx
from app.services.plan_shot2 import (
    MIN_SHOT2_VIDEO_PROMPT_LEN,
    SHOT2_VIDEO_PROMPT_ATTR,
    disk_has_shot2_video,
    find_shot2_image,
    read_shot2_columns,
)
from app.services.outsee_retry import generate_video_with_retries
from app.services.step_cancel import StepCancelledError, consume_stop, raise_if_cancelled


async def _scene_video_file_on_disk(
    session: AsyncSession,
    project_id: int,
    frame_id: int,
    *,
    shot: int = 1,
) -> Path | None:
    """scene_video shot_01 или shot_02 (meta.shot / ``_s2_`` в имени файла)."""
    arts = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.frame_id == frame_id,
                Artifact.kind == ArtifactKind.scene_video,
            )
            .order_by(Artifact.id.desc())
        )
    ).scalars().all()
    for art in arts:
        if not art.path:
            continue
        path = Path(art.path)
        if not path.is_file():
            continue
        meta_shot = (art.meta or {}).get("shot", 1)
        path_shot = 2 if "_s2_" in path.name else 1
        effective = meta_shot if meta_shot in (1, 2) else path_shot
        if effective == shot:
            return path
    return None


def resolve_scene_image_path(
    *,
    artifact_path: str | None,
    scenes_dir: Path,
    frame_number: int,
) -> Path | None:
    """Стартовый кадр для outsee: артефакт из БД или актуальный frame_NNN_*.png на диске."""
    if artifact_path:
        path = Path(artifact_path)
        if path.is_file():
            return path
    disk = newest_frame_image_path(scenes_dir, frame_number)
    if disk is not None and is_valid_scene_image(disk):
        return disk
    return None


def _skip_frame_video_generation(fr: Frame, has_video_file: bool) -> bool:
    """Не гонять outsee, если клип уже есть или кадр финально одобрен."""
    if fr.status in (FrameStatus.video_approved, FrameStatus.done):
        return True
    if has_video_file:
        return True
    return False


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_videos:
        return
    logger.info("[#{}] generate_videos starting", project.id)

    img_recovered = await recover_scene_images_from_disk(session, project)
    if img_recovered:
        logger.info(
            "[#{}] generate_videos: scene_image с диска для кадров {}",
            project.id,
            img_recovered[:40],
        )

    recovered = await recover_scene_videos_from_disk(session, project)
    if recovered:
        logger.info(
            "[#{}] generate_videos: подхвачены клипы с диска {}",
            project.id,
            recovered,
        )

    scenes_dir = project.data_dir / "scenes"

    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()

    out_dir = project.data_dir / "videos"

    # Настройки видео из проекта (с дефолтами).
    vg = VIDEO_GENERATORS_BY_ID.get(
        project.video_generator or DEFAULTS["video_generator"]
    )
    vr_o = VIDEO_RESOLUTIONS_BY_ID.get(
        project.video_resolution or DEFAULTS["video_resolution"]
    )
    ar = ASPECT_RATIOS_BY_ID.get(
        project.aspect_ratio or DEFAULTS["aspect_ratio"]
    )
    video_model_slug = vg.outsee_slug if vg else None
    video_res_slug = vr_o.outsee_slug if vr_o else None
    aspect_slug = ar.outsee_slug if ar else "9:16"

    async with browser_session() as bs:
        outsee = OutseeBot(bs)
        # `gpt` — для GPT-rewrite внутри generate_video_with_retries:
        # 3 попытки → Kling 2.5 Turbo 720p (тот же aspect) → GPT-rewrite → ещё 3.
        gpt = ChatGPTBot(bs)

        skipped = 0
        generated = 0
        shot2_generated = 0
        shot2_skipped = 0
        session_clip_paths: list[Path] = []
        try:
            for fr in frames:
                # ⏹ Остановить — проверка между кадрами.
                raise_if_cancelled(project.id)
                clip_path = await _scene_video_file_on_disk(
                    session, project.id, fr.id
                )
                has_video = clip_path is not None
                if _skip_frame_video_generation(fr, has_video):
                    skipped += 1
                    logger.info(
                        "[#{}] frame {} skip video — клип уже есть (status={}, {})",
                        project.id,
                        fr.number,
                        fr.status.value,
                        clip_path,
                    )
                    if (
                        has_video
                        and fr.status
                        not in (
                            FrameStatus.video_generated,
                            FrameStatus.video_approved,
                            FrameStatus.done,
                        )
                    ):
                        fr.status = FrameStatus.video_generated
                        await session.flush()
                    continue
                if not fr.animation_prompt:
                    raise RuntimeError(f"у кадра {fr.number} нет animation_prompt")

                # найдём картинку этого кадра (scene_image)
                img = (
                    await session.execute(
                        select(Artifact)
                        .where(
                            Artifact.project_id == project.id,
                            Artifact.frame_id == fr.id,
                            Artifact.kind == ArtifactKind.scene_image,
                        )
                        .order_by(Artifact.id.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                start_frame_path = resolve_scene_image_path(
                    artifact_path=img.path if img else None,
                    scenes_dir=scenes_dir,
                    frame_number=fr.number,
                )
                if start_frame_path is None:
                    raise RuntimeError(
                        f"у кадра {fr.number} нет картинки на диске "
                        f"(БД: {img.path if img else '—'})"
                    )
                if img is None or Path(img.path) != start_frame_path:
                    logger.warning(
                        "[#{}] frame {}: стартовый кадр с диска {} "
                        "(артефакт БД: {})",
                        project.id,
                        fr.number,
                        start_frame_path.name,
                        Path(img.path).name if img and img.path else "—",
                    )

                short_uuid = uuid.uuid4().hex[:8]
                file_path = out_dir / f"clip_{fr.number:03d}_{short_uuid}.mp4"
                duplicate_check_paths: list[Path] = []
                if fr.number > 1:
                    duplicate_check_paths.extend(
                        p
                        for p in out_dir.glob(f"clip_{fr.number - 1:03d}_*.mp4")
                        if p.is_file()
                    )
                duplicate_check_paths.extend(
                    p
                    for p in out_dir.glob(f"clip_{fr.number:03d}_*.mp4")
                    if p.is_file()
                )
                duplicate_check_paths.extend(session_clip_paths)
                duplicate_check_paths = list(
                    dict.fromkeys(p.resolve() for p in duplicate_check_paths)
                )
                prompt_id_prefix = build_gen_id_prefix(
                    project.id, fr.number, short_uuid
                )
                # Relax (Безлимит): None = не задан → включаем по умолчанию.
                # False = пользователь явно отключил.
                video_relax = project.video_relax is not False
                # 3× исходная модель → Kling 2.5 Turbo 720p → GPT-rewrite → ещё 3×.
                result = await generate_video_with_retries(
                    outsee, gpt,
                    prompt=fr.animation_prompt,
                    out_path=file_path,
                    max_attempts_per_prompt=3,
                    gpt_rewrite=True,
                    project_id=project.id,
                    start_frame=start_frame_path,
                    aspect_ratio=aspect_slug,
                    timeout=1200,
                    model_slug=video_model_slug,
                    resolution=video_res_slug,
                    relax=video_relax,
                    prompt_id_prefix=prompt_id_prefix,
                    duplicate_check_paths=duplicate_check_paths,
                )
                session.add(
                    Artifact(
                        project_id=project.id,
                        frame_id=fr.id,
                        kind=ArtifactKind.scene_video,
                        uuid=uuid.uuid4().hex,
                        path=str(result.file_path),
                        meta={"shot": 1},
                    )
                )
                fr.status = FrameStatus.video_generated
                await session.flush()
                generated += 1
                session_clip_paths.append(Path(result.file_path))
                logger.info("[#{}] frame {} video: {}", project.id, fr.number, result.file_path)
                try:
                    from app.services.event_bus import publish_project_event
                    await publish_project_event(
                        project.id,
                        event_type="video_generated",
                        payload={"frame_number": fr.number, "path": str(result.file_path)},
                    )
                except Exception:  # noqa: BLE001
                    pass
        except StepCancelledError as e:
            consume_stop(project.id)
            logger.info("[#{}] generate_videos: {} — выхожу из цикла",
                        project.id, e)
            try:
                await session.refresh(project)
            except Exception:  # noqa: BLE001
                logger.warning("[#{}] не смог refresh project после ⏹", project.id)
            return
        except asyncio.CancelledError:
            logger.info("[#{}] generate_videos: hard-cancel (⏹)", project.id)
            try:
                await session.refresh(project)
            except Exception:  # noqa: BLE001
                pass
            raise

        shot2_generated = 0
        shot2_skipped = 0
        xlsx_path = project.data_dir / "project.xlsx"
        shot2_by = read_shot2_columns(xlsx_path) if xlsx_path.is_file() else {}
        try:
            for fr in frames:
                raise_if_cancelled(project.id)
                info = shot2_by.get(fr.number)
                if info is None or not info.has_shot2:
                    continue
                if disk_has_shot2_video(out_dir, fr.number):
                    shot2_skipped += 1
                    continue
                s2_img = find_shot2_image(scenes_dir, fr.number)
                if s2_img is None:
                    logger.warning(
                        "[#{}] frame {} shot_02 video: нет PNG shot_02 — skip",
                        project.id,
                        fr.number,
                    )
                    continue
                prompt2 = animation_prompt_shot2_in_plan_xlsx(project, fr.number)
                if len(prompt2) < MIN_SHOT2_VIDEO_PROMPT_LEN:
                    attrs = fr.attrs or {}
                    prompt2 = (attrs.get(SHOT2_VIDEO_PROMPT_ATTR) or "").strip()
                if len(prompt2) < MIN_SHOT2_VIDEO_PROMPT_LEN:
                    logger.warning(
                        "[#{}] frame {} shot_02 video: нет промта plan R64 — skip",
                        project.id,
                        fr.number,
                    )
                    continue
                shot1_clip = await _scene_video_file_on_disk(
                    session, project.id, fr.id, shot=1
                )
                if shot1_clip is None:
                    logger.warning(
                        "[#{}] frame {} shot_02 video: нет clip shot_01 — skip",
                        project.id,
                        fr.number,
                    )
                    continue

                short_uuid = uuid.uuid4().hex[:8]
                file_path = out_dir / f"clip_{fr.number:03d}_s2_{short_uuid}.mp4"
                prompt_id_prefix = build_gen_id_prefix(
                    project.id, fr.number, short_uuid
                )
                video_relax = project.video_relax is not False
                result2 = await generate_video_with_retries(
                    outsee,
                    gpt,
                    prompt=prompt2,
                    out_path=file_path,
                    max_attempts_per_prompt=3,
                    gpt_rewrite=True,
                    project_id=project.id,
                    start_frame=s2_img,
                    aspect_ratio=aspect_slug,
                    timeout=1200,
                    model_slug=video_model_slug,
                    resolution=video_res_slug,
                    relax=video_relax,
                    prompt_id_prefix=prompt_id_prefix,
                    duplicate_check_paths=session_clip_paths,
                )
                session.add(
                    Artifact(
                        project_id=project.id,
                        frame_id=fr.id,
                        kind=ArtifactKind.scene_video,
                        uuid=uuid.uuid4().hex,
                        path=str(result2.file_path),
                        meta={"shot": 2},
                    )
                )
                await session.flush()
                shot2_generated += 1
                session_clip_paths.append(Path(result2.file_path))
                logger.info(
                    "[#{}] frame {} shot_02 video: {}",
                    project.id,
                    fr.number,
                    result2.file_path,
                )
        except StepCancelledError as e:
            consume_stop(project.id)
            logger.info(
                "[#{}] generate_videos shot_02: {} — выхожу",
                project.id,
                e,
            )
            try:
                await session.refresh(project)
            except Exception:  # noqa: BLE001
                pass
            return
        except asyncio.CancelledError:
            raise

    logger.info(
        "[#{}] generate_videos done: shot_01 gen={} skip={}; shot_02 gen={} skip={}",
        project.id,
        generated,
        skipped,
        shot2_generated,
        shot2_skipped,
    )
    if not frames:
        logger.warning("[#{}] generate_videos: нет кадров — split не делали?", project.id)
    elif generated == 0 and skipped == len(frames):
        logger.info(
            "[#{}] generate_videos: все кадры уже с клипом — videos_ready",
            project.id,
        )

    raise_if_cancelled(project.id)
    await session.refresh(project)
    if project.status is not ProjectStatus.generating_videos:
        logger.info(
            "[#{}] generate_videos: статус уже {} — не ставлю videos_ready (⏹?)",
            project.id,
            project.status.value,
        )
        return

    from app.services.post_step_validate import finalize_or_retry

    if not await finalize_or_retry(
        session,
        project,
        step="video",
        ready_status=ProjectStatus.videos_ready,
        running_status=ProjectStatus.generating_videos,
    ):
        return

    project.status = ProjectStatus.videos_ready
    await session.flush()

    await send_hitl_text(
        bot, session, project,
        kind=HITLKind.approve_videos,
        title=f"Клипы #{project.id}",
        text=(
            f"Готово {len(frames)} клипов по 8 сек. "
            f"Папка: `{out_dir}`. "
            "Одобри, если всё ок — начну сборку аудио и финала."
        ),
        payload={"step": "videos", "count": len(frames)},
    )
