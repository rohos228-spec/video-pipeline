"""Шаг 9: клипы Outsee veo — потоки общие с картинками (meta.img_streams / outsee)."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
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
from app.services.animation_prompt_gpt import animation_prompt_shot2_in_plan_xlsx
from app.services.artifact_recovery import (
    recover_scene_images_from_disk,
    recover_scene_videos_from_disk,
)
from app.services.gpt_client import get_gpt_client
from app.services.hitl import send_hitl_text
from app.services.img_streams import (
    VIDEO_INFLIGHT_ATTR,
    acquire_outsee_slot,
    get_outsee_streams,
)
from app.services.outsee_retry import generate_video_with_retries
from app.services.plan_shot2 import (
    MIN_SHOT2_VIDEO_PROMPT_LEN,
    SHOT2_VIDEO_PROMPT_ATTR,
    disk_has_shot2_video,
    effective_shot_from_artifact,
    find_shot1_image,
    find_shot2_image,
    read_shot2_columns,
)
from app.services.scan_frames import is_valid_scene_image
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
        if effective_shot_from_artifact(art.meta, path) == shot:
            return path
    return None


def resolve_scene_image_path(
    *,
    artifact_path: str | None,
    scenes_dir: Path,
    frame_number: int,
) -> Path | None:
    """Стартовый кадр shot_01 для outsee: артефакт из БД или frame_NNN_*.png (не s2)."""
    if artifact_path:
        path = Path(artifact_path)
        if path.is_file() and "_s2_" not in path.name:
            return path
    disk = find_shot1_image(scenes_dir, frame_number)
    if disk is not None and is_valid_scene_image(disk):
        return disk
    return None


def _skip_frame_video_generation(fr: Frame, has_video_file: bool) -> bool:
    """Не гонять outsee, если клип уже есть на диске."""
    return bool(has_video_file)


def _clear_video_inflight(frame: Frame) -> None:
    attrs = dict(frame.attrs or {})
    if attrs.pop(VIDEO_INFLIGHT_ATTR, None) is not None:
        frame.attrs = attrs


def _video_opts(project: Project) -> tuple[str | None, str | None, str, bool]:
    vg = VIDEO_GENERATORS_BY_ID.get(
        project.video_generator or DEFAULTS["video_generator"]
    )
    vr_o = VIDEO_RESOLUTIONS_BY_ID.get(
        project.video_resolution or DEFAULTS["video_resolution"]
    )
    ar = ASPECT_RATIOS_BY_ID.get(project.aspect_ratio or DEFAULTS["aspect_ratio"])
    return (
        vg.outsee_slug if vg else None,
        vr_o.outsee_slug if vr_o else None,
        ar.outsee_slug if ar else "9:16",
        project.video_relax is not False,
    )


async def _shot1_start_frame(
    session: AsyncSession, project: Project, fr: Frame, scenes_dir: Path
) -> Path:
    img = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project.id,
                Artifact.frame_id == fr.id,
                Artifact.kind == ArtifactKind.scene_image,
            )
            .order_by(Artifact.id.desc())
        )
    ).scalars().all()
    shot1_artifact_path: str | None = None
    for art in img:
        if not art.path:
            continue
        if effective_shot_from_artifact(art.meta, art.path) != 1:
            continue
        if "_s2_" in Path(art.path).name:
            continue
        shot1_artifact_path = art.path
        break
    start = resolve_scene_image_path(
        artifact_path=shot1_artifact_path,
        scenes_dir=scenes_dir,
        frame_number=fr.number,
    )
    if start is None:
        raise RuntimeError(
            f"у кадра {fr.number} нет картинки shot_01 на диске "
            f"(БД: {shot1_artifact_path or '—'})"
        )
    return start


def _dup_paths(out_dir: Path, frame_number: int, extra: list[Path]) -> list[Path]:
    paths: list[Path] = []
    if frame_number > 1:
        paths.extend(
            p for p in out_dir.glob(f"clip_{frame_number - 1:03d}_*.mp4") if p.is_file()
        )
    paths.extend(
        p for p in out_dir.glob(f"clip_{frame_number:03d}_*.mp4") if p.is_file()
    )
    paths.extend(extra)
    return list(dict.fromkeys(p.resolve() for p in paths))


async def _claim_shot1_video_batch(
    session: AsyncSession,
    project_id: int,
    *,
    limit: int,
) -> list[Frame]:
    if limit < 1:
        return []
    # populate_existing: после parallel SessionLocal() attrs.inflight иначе stale
    # в identity map родителя → кадры навсегда пропускаются.
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project_id)
            .order_by(Frame.number)
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    claimed: list[Frame] = []
    for fr in frames:
        attrs = dict(fr.attrs or {})
        if attrs.get(VIDEO_INFLIGHT_ATTR):
            continue
        clip = await _scene_video_file_on_disk(session, project_id, fr.id, shot=1)
        if _skip_frame_video_generation(fr, clip is not None):
            if clip is not None and fr.status not in (
                FrameStatus.video_generated,
                FrameStatus.video_approved,
                FrameStatus.done,
            ):
                fr.status = FrameStatus.video_generated
            continue
        if not (fr.animation_prompt or "").strip():
            continue
        attrs[VIDEO_INFLIGHT_ATTR] = True
        fr.attrs = attrs
        claimed.append(fr)
        if len(claimed) >= limit:
            break
    if claimed:
        await session.flush()
    return claimed


async def _claim_shot2_video_batch(
    session: AsyncSession,
    project: Project,
    out_dir: Path,
    scenes_dir: Path,
    *,
    limit: int,
) -> list[tuple[Frame, str, Path]]:
    if limit < 1:
        return []
    xlsx_path = project.data_dir / "project.xlsx"
    shot2_by = read_shot2_columns(xlsx_path) if xlsx_path.is_file() else {}
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number)
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    claimed: list[tuple[Frame, str, Path]] = []
    for fr in frames:
        info = shot2_by.get(fr.number)
        if info is None or not info.has_shot2:
            continue
        attrs = dict(fr.attrs or {})
        if attrs.get(VIDEO_INFLIGHT_ATTR):
            continue
        if disk_has_shot2_video(out_dir, fr.number):
            continue
        s2_img = find_shot2_image(scenes_dir, fr.number)
        if s2_img is None:
            continue
        prompt2 = animation_prompt_shot2_in_plan_xlsx(project, fr.number)
        if len(prompt2) < MIN_SHOT2_VIDEO_PROMPT_LEN:
            prompt2 = (attrs.get(SHOT2_VIDEO_PROMPT_ATTR) or "").strip()
        if len(prompt2) < MIN_SHOT2_VIDEO_PROMPT_LEN:
            continue
        shot1 = await _scene_video_file_on_disk(session, project.id, fr.id, shot=1)
        if shot1 is None:
            continue
        attrs[VIDEO_INFLIGHT_ATTR] = True
        fr.attrs = attrs
        claimed.append((fr, prompt2, s2_img))
        if len(claimed) >= limit:
            break
    if claimed:
        await session.flush()
    return claimed


async def _generate_shot1_one(
    *,
    session: AsyncSession,
    outsee: OutseeBot,
    gpt: Any,
    project: Project,
    fr: Frame,
    out_dir: Path,
    scenes_dir: Path,
    session_clip_paths: list[Path],
    clips_lock: asyncio.Lock,
) -> Path:
    if not fr.animation_prompt:
        raise RuntimeError(f"у кадра {fr.number} нет animation_prompt")
    start = await _shot1_start_frame(session, project, fr, scenes_dir)
    short_uuid = uuid.uuid4().hex[:8]
    file_path = out_dir / f"clip_{fr.number:03d}_{short_uuid}.mp4"
    async with clips_lock:
        dups = _dup_paths(out_dir, fr.number, list(session_clip_paths))
    model_slug, res_slug, aspect, relax = _video_opts(project)
    result = await generate_video_with_retries(
        outsee,
        gpt,
        prompt=fr.animation_prompt,
        out_path=file_path,
        max_attempts_per_prompt=3,
        gpt_rewrite=True,
        project_id=project.id,
        start_frame=start,
        aspect_ratio=aspect,
        timeout=1200,
        model_slug=model_slug,
        resolution=res_slug,
        relax=relax,
        generate_audio=False,
        prompt_id_prefix=build_gen_id_prefix(project.id, fr.number, short_uuid),
        duplicate_check_paths=dups,
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
    out = Path(result.file_path)
    async with clips_lock:
        session_clip_paths.append(out)
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
    return out


async def _generate_shot2_one(
    *,
    session: AsyncSession,
    outsee: OutseeBot,
    gpt: Any,
    project: Project,
    fr: Frame,
    prompt2: str,
    s2_img: Path,
    out_dir: Path,
    session_clip_paths: list[Path],
    clips_lock: asyncio.Lock,
) -> Path:
    short_uuid = uuid.uuid4().hex[:8]
    file_path = out_dir / f"clip_{fr.number:03d}_s2_{short_uuid}.mp4"
    async with clips_lock:
        dups = list(session_clip_paths)
    model_slug, res_slug, aspect, relax = _video_opts(project)
    result = await generate_video_with_retries(
        outsee,
        gpt,
        prompt=prompt2,
        out_path=file_path,
        max_attempts_per_prompt=3,
        gpt_rewrite=True,
        project_id=project.id,
        start_frame=s2_img,
        aspect_ratio=aspect,
        timeout=1200,
        model_slug=model_slug,
        resolution=res_slug,
        relax=relax,
        generate_audio=False,
        prompt_id_prefix=build_gen_id_prefix(project.id, fr.number, short_uuid)
        + "-S2",
        duplicate_check_paths=dups,
    )
    session.add(
        Artifact(
            project_id=project.id,
            frame_id=fr.id,
            kind=ArtifactKind.scene_video,
            uuid=uuid.uuid4().hex,
            path=str(result.file_path),
            meta={"shot": 2},
        )
    )
    await session.flush()
    out = Path(result.file_path)
    async with clips_lock:
        session_clip_paths.append(out)
    logger.info(
        "[#{}] frame {} shot_02 video: {}",
        project.id,
        fr.number,
        result.file_path,
    )
    return out


async def _shot1_job(
    *,
    project_id: int,
    frame_id: int,
    out_dir: Path,
    scenes_dir: Path,
    outsee: OutseeBot,
    gpt: Any,
    session_clip_paths: list[Path],
    clips_lock: asyncio.Lock,
) -> None:
    from app.db import SessionLocal

    async with acquire_outsee_slot():
        async with SessionLocal() as session:
            project = await session.get(Project, project_id)
            fr = await session.get(Frame, frame_id)
            if project is None or fr is None:
                return
            try:
                await _generate_shot1_one(
                    session=session,
                    outsee=outsee,
                    gpt=gpt,
                    project=project,
                    fr=fr,
                    out_dir=out_dir,
                    scenes_dir=scenes_dir,
                    session_clip_paths=session_clip_paths,
                    clips_lock=clips_lock,
                )
                await session.commit()
            finally:
                try:
                    await session.refresh(fr)
                except Exception:  # noqa: BLE001
                    pass
                _clear_video_inflight(fr)
                try:
                    await session.commit()
                except Exception:  # noqa: BLE001
                    pass


async def _shot2_job(
    *,
    project_id: int,
    frame_id: int,
    prompt2: str,
    s2_img: Path,
    out_dir: Path,
    outsee: OutseeBot,
    gpt: Any,
    session_clip_paths: list[Path],
    clips_lock: asyncio.Lock,
) -> None:
    from app.db import SessionLocal

    async with acquire_outsee_slot():
        async with SessionLocal() as session:
            project = await session.get(Project, project_id)
            fr = await session.get(Frame, frame_id)
            if project is None or fr is None:
                return
            try:
                await _generate_shot2_one(
                    session=session,
                    outsee=outsee,
                    gpt=gpt,
                    project=project,
                    fr=fr,
                    prompt2=prompt2,
                    s2_img=s2_img,
                    out_dir=out_dir,
                    session_clip_paths=session_clip_paths,
                    clips_lock=clips_lock,
                )
                await session.commit()
            finally:
                try:
                    await session.refresh(fr)
                except Exception:  # noqa: BLE001
                    pass
                _clear_video_inflight(fr)
                try:
                    await session.commit()
                except Exception:  # noqa: BLE001
                    pass


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_videos:
        return
    project_id = project.id
    logger.info("[#{}] generate_videos starting", project_id)

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
    out_dir.mkdir(parents=True, exist_ok=True)

    streams = get_outsee_streams(project)
    logger.info(
        "[#{}] generate_videos: outsee_streams={} (общий лимит с img/Create)",
        project.id,
        streams,
    )

    for fr in frames:
        attrs = dict(fr.attrs or {})
        if attrs.pop(VIDEO_INFLIGHT_ATTR, None) is not None:
            fr.attrs = attrs
    await session.flush()

    if streams == 0:
        missing = []
        for fr in frames:
            clip = await _scene_video_file_on_disk(session, project.id, fr.id, shot=1)
            if clip is None and (fr.animation_prompt or "").strip():
                missing.append(fr.number)
        if missing:
            raise RuntimeError(
                f"outsee_streams/img_streams=0: провайдер выкл, нет клипов "
                f"{missing[:12]}{'…' if len(missing) > 12 else ''}. "
                f"Поставь meta.img_streams 1..4."
            )
        logger.info(
            "[#{}] generate_videos: streams=0 — все клипы на диске, outsee не нужен",
            project.id,
        )
        generated = 0
        shot2_generated = 0
    else:
        generated = 0
        shot2_generated = 0
        session_clip_paths: list[Path] = []
        clips_lock = asyncio.Lock()

        async with browser_session() as bs:
            outsee = OutseeBot(bs)
            gpt = get_gpt_client()
            try:
                while True:
                    raise_if_cancelled(project.id)
                    batch = await _claim_shot1_video_batch(
                        session, project.id, limit=streams
                    )
                    if not batch:
                        break
                    logger.info(
                        "[#{}] generate_videos: shot1 batch n={} frames={}",
                        project.id,
                        len(batch),
                        [f.number for f in batch],
                    )
                    if streams <= 1:
                        fr = batch[0]
                        try:
                            async with acquire_outsee_slot():
                                await _generate_shot1_one(
                                    session=session,
                                    outsee=outsee,
                                    gpt=gpt,
                                    project=project,
                                    fr=fr,
                                    out_dir=out_dir,
                                    scenes_dir=scenes_dir,
                                    session_clip_paths=session_clip_paths,
                                    clips_lock=clips_lock,
                                )
                            generated += 1
                        finally:
                            _clear_video_inflight(fr)
                            await session.flush()
                        continue

                    await session.commit()
                    results = await asyncio.gather(
                        *[
                            _shot1_job(
                                project_id=project.id,
                                frame_id=fr.id,
                                out_dir=out_dir,
                                scenes_dir=scenes_dir,
                                outsee=outsee,
                                gpt=gpt,
                                session_clip_paths=session_clip_paths,
                                clips_lock=clips_lock,
                            )
                            for fr in batch
                        ],
                        return_exceptions=True,
                    )
                    # expire_all: identity map после parallel SessionLocal() stale;
                    # PK project.id после expire нельзя трогать — lazy-load → MissingGreenlet.
                    session.expire_all()
                    fail_n = 0
                    for r in results:
                        if isinstance(r, StepCancelledError):
                            raise r
                        if isinstance(r, Exception):
                            fail_n += 1
                            logger.error(
                                "[#{}] video stream worker failed: {}",
                                project_id,
                                r,
                            )
                        else:
                            generated += 1
                    # После массового fail (часто «лимит 4» из-за ghost jobs)
                    # дать Outsee освободить слоты, прежде чем claim следующей пачки.
                    if fail_n and fail_n >= len(results):
                        from app.services.outsee_retry import (
                            _is_concurrency_limit_error,
                        )

                        if any(
                            isinstance(r, Exception) and _is_concurrency_limit_error(r)
                            for r in results
                        ):
                            logger.warning(
                                "[#{}] generate_videos: вся пачка упёрлась в "
                                "лимит Outsee — пауза 60с перед следующей",
                                project_id,
                            )
                            await asyncio.sleep(60)
                    await session.refresh(project)

                # shot_02
                while True:
                    raise_if_cancelled(project.id)
                    batch2 = await _claim_shot2_video_batch(
                        session,
                        project,
                        out_dir,
                        scenes_dir,
                        limit=streams,
                    )
                    if not batch2:
                        break
                    logger.info(
                        "[#{}] generate_videos: shot2 batch n={} frames={}",
                        project.id,
                        len(batch2),
                        [f.number for f, _, _ in batch2],
                    )
                    if streams <= 1:
                        fr, prompt2, s2_img = batch2[0]
                        try:
                            async with acquire_outsee_slot():
                                await _generate_shot2_one(
                                    session=session,
                                    outsee=outsee,
                                    gpt=gpt,
                                    project=project,
                                    fr=fr,
                                    prompt2=prompt2,
                                    s2_img=s2_img,
                                    out_dir=out_dir,
                                    session_clip_paths=session_clip_paths,
                                    clips_lock=clips_lock,
                                )
                            shot2_generated += 1
                        finally:
                            _clear_video_inflight(fr)
                            await session.flush()
                        continue

                    await session.commit()
                    results2 = await asyncio.gather(
                        *[
                            _shot2_job(
                                project_id=project.id,
                                frame_id=fr.id,
                                prompt2=prompt2,
                                s2_img=s2_img,
                                out_dir=out_dir,
                                outsee=outsee,
                                gpt=gpt,
                                session_clip_paths=session_clip_paths,
                                clips_lock=clips_lock,
                            )
                            for fr, prompt2, s2_img in batch2
                        ],
                        return_exceptions=True,
                    )
                    session.expire_all()
                    for r in results2:
                        if isinstance(r, StepCancelledError):
                            raise r
                        if isinstance(r, Exception):
                            logger.error(
                                "[#{}] video s2 stream worker failed: {}",
                                project_id,
                                r,
                            )
                        else:
                            shot2_generated += 1
                    await session.refresh(project)

            except StepCancelledError as e:
                consume_stop(project.id)
                logger.info(
                    "[#{}] generate_videos: {} — выхожу из цикла", project.id, e
                )
                try:
                    await session.refresh(project)
                except Exception:  # noqa: BLE001
                    pass
                return
            except asyncio.CancelledError:
                logger.info("[#{}] generate_videos: hard-cancel (⏹)", project.id)
                try:
                    await session.refresh(project)
                except Exception:  # noqa: BLE001
                    pass
                raise

    logger.info(
        "[#{}] generate_videos done: shot_01 gen={} shot_02 gen={} streams={}",
        project.id,
        generated,
        shot2_generated,
        streams,
    )
    if not frames:
        logger.warning("[#{}] generate_videos: нет кадров — split не делали?", project.id)

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

    try:
        from app.services.vision_check_loop import (
            maybe_return_to_check_after_vision_ready,
            vision_check_loop_active,
        )

        if vision_check_loop_active(project):
            nxt = await maybe_return_to_check_after_vision_ready(session, project)
            if nxt is not None:
                project.status = nxt
                await session.flush()
                logger.info(
                    "[#{}] generate_videos: vision_check_loop → {}",
                    project.id,
                    nxt.value,
                )
                return
    except Exception:  # noqa: BLE001
        logger.exception(
            "[#{}] generate_videos: vision_check_loop return failed",
            project.id,
        )

    await send_hitl_text(
        bot,
        session,
        project,
        kind=HITLKind.approve_videos,
        title=f"Клипы #{project.id}",
        text=(
            f"Готово {len(frames)} клипов по 8 сек. "
            f"Папка: `{out_dir}`. "
            "Одобри, если всё ок — начну сборку аудио и финала."
        ),
        payload={"step": "videos", "count": len(frames)},
    )
