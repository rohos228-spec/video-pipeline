"""Шаг 11: финальная сборка — видео и субтитры только по озвучке."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    HITLKind,
    Project,
    ProjectStatus,
)
from app.services.artifact_recovery import ensure_whisper_words, recover_before_assemble
from app.services.assembly import ClipSpec, assemble, make_simple_ass
from app.services.bgm import resolve_bgm
from app.services.frame_audio import (
    _voiceover_cells_for_frames,
    build_assembly_timeline,
    has_all_frame_audio,
)
from app.services.hitl import send_hitl_video
from app.services.mapper import FrameTiming
from app.services.media_probe import probe_duration, probe_video_size
from app.services.step_data_guard import can_enter_running
from app.services.subtitles import build_subtitle_cues_from_cells
from app.services.whisper import (
    WordTS,
    transcribe_words,
    whisper_available,
    whisper_words_fresh_for_audio,
)
from app.services.node_step_params import (
    post_voiceover_tail_seconds_for_project,
    subtitles_enabled_for_project,
)
from app.settings import settings
from app.services.shot2_timeline import build_assembly_clip_specs
from app.services.plan_shot2 import read_shot2_columns
from app.storage.plan_sheet_v8 import read_plan_voiceover_cells


async def _scene_video_path(
    session: AsyncSession,
    project_id: int,
    frame_id: int,
    *,
    shot: int = 1,
) -> Path | None:
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


def _scale_whisper_words(words: list[WordTS], factor: float) -> list[WordTS]:
    if abs(factor - 1.0) < 0.001:
        return words
    return [
        WordTS(
            word=w.word,
            start=round(w.start * factor, 3),
            end=round(w.end * factor, 3),
            prob=w.prob,
        )
        for w in words
    ]


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.assembling:
        return
    logger.info("[#{}] assemble starting", project.id)

    await recover_before_assemble(session, project)
    ok, reason, rollback = await can_enter_running(
        session, project, ProjectStatus.assembling
    )
    if not ok:
        project.status = rollback or ProjectStatus.generating_audio
        await session.flush()
        raise RuntimeError(
            f"сборка невозможна: {reason}. Статус → {project.status.value}"
        )

    frames_all = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    if not frames_all:
        raise RuntimeError("нет кадров")

    frames: list[Frame] = []
    skipped_no_video: list[int] = []
    for fr in frames_all:
        video_art = (
            await session.execute(
                select(Artifact)
                .where(
                    Artifact.project_id == project.id,
                    Artifact.frame_id == fr.id,
                    Artifact.kind == ArtifactKind.scene_video,
                )
                .order_by(Artifact.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if video_art is not None and Path(video_art.path).is_file():
            frames.append(fr)
        elif (fr.voiceover_text or "").strip():
            skipped_no_video.append(fr.number)
    if skipped_no_video:
        logger.warning(
            "[#{}] assemble: кадры {} без клипа — не входят в финальный ролик",
            project.id,
            skipped_no_video,
        )
    if not frames:
        raise RuntimeError(
            "нет кадров с видео-клипами — сначала шаг «Видео» "
            f"(пропущены voiceover-кадры: {skipped_no_video or '—'})"
        )

    audio = (
        await session.execute(
            select(Artifact)
            .where(Artifact.project_id == project.id, Artifact.kind == ArtifactKind.audio)
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if audio is None:
        raise RuntimeError(
            "нет артефакта аудио — запустите шаг «Аудио» "
            "(voice_full*.mp3 в audio/ не зарегистрирован)"
        )

    audio_path = Path(audio.path)
    if not audio_path.is_file():
        raise RuntimeError(f"файл озвучки не найден: {audio_path}")
    audio_dir = project.data_dir / "audio"
    frame_numbers = [fr.number for fr in frames]
    audio_meta = audio.meta or {}
    per_frame_tts = (
        audio_meta.get("mode") == "per_frame"
        and audio_meta.get("source") != "disk_whisper"
        and has_all_frame_audio(audio_dir, frame_numbers)
    )
    disk_whisper = audio_meta.get("mode") == "disk_whisper" or (
        audio_meta.get("source") == "disk_whisper"
    )
    subs_enabled = subtitles_enabled_for_project(project)

    words: list[WordTS] = await ensure_whisper_words(
        session,
        project,
        audio_path,
        whisper_model=settings.whisper_model,
    )
    whisper_art = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project.id,
                Artifact.kind == ArtifactKind.whisper_words,
            )
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if subs_enabled and settings.subtitle_rewhisper_on_assemble and audio_path.is_file():
        fresh_words = whisper_words_fresh_for_audio(whisper_art, audio_path)
        if fresh_words and words:
            logger.info(
                "[#{}] assemble: words.json актуален для {} — re-whisper пропущен",
                project.id,
                audio_path.name,
            )
        elif whisper_available() and not disk_whisper:
            audio_duration = await probe_duration(audio_path)
            beam = 1 if audio_duration > 300 else 5
            logger.info(
                "[#{}] assemble: re-whisper voice_full для субтитров "
                "(без TTS, {:.1f}s, beam={})",
                project.id,
                audio_duration,
                beam,
            )
            words = await asyncio.to_thread(
                transcribe_words,
                audio_path,
                model_name=settings.whisper_model,
                language="ru",
                beam_size=beam,
            )
        elif not words:
            raise RuntimeError(
                "нет words.json и faster-whisper не установлен — "
                "pip install -e \".[whisper]\" или перезапустите шаг «Аудио»"
            )
        else:
            logger.warning(
                "[#{}] assemble: faster-whisper не установлен — субтитры из words.json. "
                "Для re-whisper: pip install -e \".[whisper]\"",
                project.id,
            )
    elif not words and not per_frame_tts:
        raise RuntimeError(
            "нет word-level таймкодов Whisper — перезапустите шаг «Аудио» перед сборкой"
        )
    if subs_enabled and not words:
        raise RuntimeError("Whisper не вернул слова для субтитров")

    cells = _voiceover_cells_for_frames(
        project,
        frames_all,
        read_plan_voiceover_cells(project, frame_numbers),
    )
    frame_set = set(frame_numbers)
    cells = [(n, t) for n, t in cells if n in frame_set]
    if not any(text.strip() for _, text in cells):
        raise RuntimeError(
            "нет текста для субтитров (строка 49 / voiceover.txt / БД кадров)"
        )

    try:
        return await _assemble_body(
            session,
            project,
            bot,
            frames=frames,
            frames_all=frames_all,
            skipped_no_video=skipped_no_video,
            audio=audio,
            audio_path=audio_path,
            audio_dir=audio_dir,
            frame_numbers=frame_numbers,
            per_frame_tts=per_frame_tts,
            subs_enabled=subs_enabled,
            words=words,
            whisper_art=whisper_art,
            cells=cells,
        )
    except Exception:
        if project.status is ProjectStatus.assembling:
            project.status = ProjectStatus.audio_ready
            await session.flush()
            logger.warning(
                "[#{}] assemble failed — status → audio_ready (запустите «Аудио», потом «Сборка»)",
                project.id,
            )
        raise


async def _assemble_body(
    session: AsyncSession,
    project: Project,
    bot: Bot,
    *,
    frames: list[Frame],
    frames_all: list[Frame],
    skipped_no_video: list[int],
    audio,
    audio_path: Path,
    audio_dir: Path,
    frame_numbers: list[int],
    per_frame_tts: bool,
    subs_enabled: bool,
    words: list[WordTS],
    whisper_art,
    cells: list[tuple[int, str]],
) -> None:
    try:
        audio_clips, audio_duration, time_scale, per_frame_audio = await build_assembly_timeline(
            audio_dir,
            audio_path,
            frame_numbers,
            cells=cells,
            words=words,
            per_frame_tts=per_frame_tts,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(str(exc)) from exc

    words = _scale_whisper_words(words, time_scale) if not per_frame_audio else words
    _ = frames_all, skipped_no_video, audio, whisper_art  # reserved for future diagnostics

    video_duration = sum(c.duration for c in audio_clips)
    logger.info(
        "[#{}] assemble: master voice {:.2f}s, {} clips, video {:.2f}s, "
        "timeline={}, burn_subs={}",
        project.id,
        audio_duration,
        len(audio_clips),
        video_duration,
        "per-frame" if per_frame_audio else "legacy-stretch",
        subs_enabled,
    )

    duration_by_frame = {c.frame_number: c.duration for c in audio_clips}
    frame_timings = [
        FrameTiming(c.frame_number, c.start_ts, c.end_ts, c.duration)
        for c in audio_clips
    ]

    shot1_paths: dict[int, Path] = {}
    shot2_paths: dict[int, Path | None] = {}
    xlsx_path = project.data_dir / "project.xlsx"
    shot2_by = read_shot2_columns(xlsx_path) if xlsx_path.is_file() else {}
    for fr in frames:
        p1 = await _scene_video_path(session, project.id, fr.id, shot=1)
        if p1 is None:
            raise RuntimeError(f"нет клипа shot_01 для кадра {fr.number}")
        shot1_paths[fr.number] = p1
        info = shot2_by.get(fr.number)
        p2 = None
        if info is not None and info.has_shot2:
            p2 = await _scene_video_path(session, project.id, fr.id, shot=2)
            if p2 is None:
                videos_dir = project.data_dir / "videos"
                from app.services.plan_shot2 import disk_has_shot2_video

                if disk_has_shot2_video(videos_dir, fr.number):
                    for path in sorted(
                        videos_dir.glob(f"clip_{fr.number:03d}_s2_*.mp4"),
                        key=lambda p: p.stat().st_mtime,
                        reverse=True,
                    ):
                        p2 = path
                        break
        shot2_paths[fr.number] = p2
        fr.start_ts = next(c.start_ts for c in audio_clips if c.frame_number == fr.number)
        fr.end_ts = next(c.end_ts for c in audio_clips if c.frame_number == fr.number)
        fr.duration_seconds = duration_by_frame[fr.number]

    clips = build_assembly_clip_specs(
        frames, shot1_paths, shot2_paths, duration_by_frame
    )

    subs_path: Path | None = None
    if subs_enabled:
        subs_dir = project.data_dir / "subs"
        subs_path = subs_dir / f"subs_{uuid.uuid4().hex[:8]}.ass"
        sub_entries = build_subtitle_cues_from_cells(
            cells,
            words,
            frame_timings,
            max_words=settings.subtitle_max_words,
            max_end_ts=audio_duration,
            lead_seconds=settings.subtitle_lead_seconds,
            chars_per_second=settings.subtitle_chars_per_second,
        )
        if not sub_entries:
            raise RuntimeError("не удалось построить субтитры из Excel + Whisper")
        ass_w, ass_h = await probe_video_size(clips[0].src)
        make_simple_ass(sub_entries, subs_path, width=ass_w, height=ass_h)
        session.add(Artifact(
            project_id=project.id, kind=ArtifactKind.subtitle,
            uuid=uuid.uuid4().hex, path=str(subs_path),
        ))
    else:
        logger.info("[#{}] assemble: субтитры выключены в настройках сборки", project.id)

    out_dir = project.data_dir / "final"
    out_path = out_dir / f"{project.slug}.mp4"
    bgm = resolve_bgm(project)
    tail_seconds = post_voiceover_tail_seconds_for_project(project)
    await assemble(
        clips,
        audio_path,
        out_path,
        subtitles_ass=subs_path,
        max_duration=audio_duration,
        tail_seconds=tail_seconds,
        bgm=bgm,
    )
    await session.flush()

    session.add(Artifact(
        project_id=project.id, kind=ArtifactKind.final_video,
        uuid=uuid.uuid4().hex, path=str(out_path),
    ))
    project.status = ProjectStatus.assembled
    await session.flush()

    from app.services.mass_factory import on_child_montage_complete

    await on_child_montage_complete(session, project)

    await send_hitl_video(
        bot, session, project,
        kind=HITLKind.approve_final,
        video_path=str(out_path),
        caption=f"Финальный ролик #{project.id} готов. Одобрить и публиковать?",
        payload={"step": "final"},
    )
