"""Шаг 11: финальная сборка — видео и субтитры только по озвучке."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
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
from app.services.artifact_recovery import recover_before_assemble
from app.services.assembly import assemble, make_simple_ass, subtitles_vf_arg, SUBTITLES_ASS_NAME
from app.services.bgm import resolve_bgm
from app.services.frame_audio import build_assembly_timeline, has_all_frame_audio
from app.services.hitl import send_hitl_video
from app.services.mapper import FrameTiming
from app.services.media_probe import probe_duration, probe_video_size
from app.services.montage.variant2 import MONTAGE_ENGINE_V2, run_variant2
from app.services.montage_board_meta import montage_meta
from app.services.montage_asr import ensure_montage_words
from app.services.node_step_params import (
    post_voiceover_tail_seconds_for_project,
    subtitles_enabled_for_project,
)
from app.services.plan_shot2 import read_shot2_columns
from app.services.shot2_timeline import build_assembly_clip_specs
from app.services.step_data_guard import can_enter_running
from app.services.subtitles import build_subtitle_cues_from_cells
from app.services.whisper import WordTS, load_words_json, transcribe_words, whisper_available
from app.settings import settings
from app.storage.plan_sheet_v8 import resolve_plan_voiceover_cells


async def _scene_video_path(
    session: AsyncSession,
    project: Project,
    frame: Frame,
    *,
    shot: int = 1,
) -> Path | None:
    """Artifact или newest-on-disk — кто свежее (mtime), тот и в монтаж."""
    from app.services.artifact_recovery import newest_disk_video
    from app.services.plan_shot2 import effective_shot_from_artifact

    arts = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project.id,
                Artifact.frame_id == frame.id,
                Artifact.kind == ArtifactKind.scene_video,
            )
            .order_by(Artifact.id.desc())
        )
    ).scalars().all()
    art_path: Path | None = None
    for art in arts:
        if not art.path:
            continue
        path = Path(art.path)
        if not path.is_file():
            continue
        if effective_shot_from_artifact(art.meta, path) == shot:
            art_path = path
            break

    disk_path = newest_disk_video(project.data_dir / "videos", frame.number, shot)
    candidates = [p for p in (art_path, disk_path) if p is not None and p.is_file()]
    if not candidates:
        return None
    by_key: dict[str, Path] = {}
    for p in candidates:
        try:
            by_key[str(p.resolve())] = p
        except OSError:
            by_key[str(p)] = p
    return max(by_key.values(), key=lambda p: p.stat().st_mtime)


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

    from app.services.montage_coexist import montage_lane_claim

    with montage_lane_claim(project.id):
        await _run_assemble(session, project, bot)


async def _run_assemble(session: AsyncSession, project: Project, bot: Bot) -> None:
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
        from app.services.ensure_frames_from_disk import bootstrap_project_frames_from_disk

        boot = await bootstrap_project_frames_from_disk(session, project, sync_xlsx=True)
        if boot.get("frames_created"):
            logger.info(
                "[#{}] assemble: bootstrap {} кадров с диска",
                project.id,
                boot["frames_created"],
            )
        frames_all = (
            await session.execute(
                select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
            )
        ).scalars().all()
    if not frames_all:
        raise RuntimeError(
            "нет кадров — положите clip_*/frame_* в videos/scenes или project.xlsx"
        )

    frames: list[Frame] = []
    skipped_no_video: list[int] = []
    for fr in frames_all:
        p1 = await _scene_video_path(session, project, fr, shot=1)
        p2 = await _scene_video_path(session, project, fr, shot=2)
        if p1 is not None or p2 is not None:
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
    frame_numbers = [fr.number for fr in frames_all]
    audio_meta = audio.meta or {}
    per_frame_tts = (
        audio_meta.get("mode") == "per_frame"
        and audio_meta.get("source") != "disk_whisper"
        and has_all_frame_audio(audio_dir, [fr.number for fr in frames])
    )
    full_voice = audio_meta.get("mode") in ("full_voice", "disk_whisper")
    subs_enabled = subtitles_enabled_for_project(project)

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
    words: list[WordTS] = []
    if whisper_art is not None:
        wp = Path(whisper_art.path) if whisper_art.path else None
        if wp and wp.is_file():
            words = load_words_json(wp)
        else:
            logger.warning(
                "[#{}] assemble: whisper_words в БД, файла нет ({}) — пересчитаем ASR",
                project.id,
                wp or "?",
            )
            await session.delete(whisper_art)
            whisper_art = None
            await session.flush()

    from app.services.nvidia_asr import looks_like_fake_uniform_timestamps

    if words and looks_like_fake_uniform_timestamps(words):
        logger.warning(
            "[#{}] assemble: фейковые ASR words (0.25s) — удаляем кэш и пересчитываем",
            project.id,
        )
        words = []
        if whisper_art is not None:
            wp = Path(whisper_art.path) if whisper_art.path else None
            if wp and wp.is_file():
                wp.unlink(missing_ok=True)
            await session.delete(whisper_art)
            whisper_art = None
            await session.flush()

    cells, voice_src = await resolve_plan_voiceover_cells(session, project, frame_numbers)
    if not any(text.strip() for _, text in cells):
        raise RuntimeError(
            "не удалось прочитать текст кадров из project.xlsx (лист «план», строка 49). "
            "Сохраните файл и закройте Excel."
        )
    if voice_src == "db-frames":
        logger.info("[#{}] assemble: voiceover из БД (R49 в xlsx пуст)", project.id)

    ts_cells: list[tuple[int, str]] | None = None
    ts_row: int | None = None

    if not per_frame_tts:
        from app.services.montage.r15 import resolve_montage_frame_numbers
        from app.services.plan_timestamps import (
            count_parsed_timestamp_cells,
            ensure_r15_from_asr,
        )

        montage_frame_numbers = resolve_montage_frame_numbers(project, frame_numbers)
        montage_cells = [
            (n, text) for n, text in cells if n in set(montage_frame_numbers)
        ]
        if len(montage_cells) < len(montage_frame_numbers):
            by_num = dict(cells)
            montage_cells = [
                (n, by_num.get(n, "")) for n in montage_frame_numbers
            ]

        if not words:
            words = await ensure_montage_words(
                session,
                project,
                audio_path=audio_path,
                audio_dir=audio_dir,
                frame_numbers=montage_frame_numbers,
            )

        xlsx_path = project.data_dir / "project.xlsx"
        ts_cells, ts_row = await ensure_r15_from_asr(
            project,
            frame_numbers=montage_frame_numbers,
            cells=montage_cells,
            words=words,
            voice_full_path=audio_path,
        )
        _filled, parsed_n, bad = count_parsed_timestamp_cells(ts_cells)
        if parsed_n < len(montage_frame_numbers):
            sample = ", ".join(str(n) for n in bad[:5]) if bad else "—"
            raise RuntimeError(
                f"[#{project.id}] монтаж только по Excel R{ts_row}: "
                f"прочитано {parsed_n}/{len(montage_frame_numbers)} меток из {xlsx_path}. "
                f"Сохраните файл, закройте Excel. Битые/пустые: {sample}"
            )
        logger.info(
            "[#{}] preflight Excel R{}: {} меток OK ({})",
            project.id,
            ts_row,
            parsed_n,
            xlsx_path,
        )

    if not words and not per_frame_tts:
        words = await ensure_montage_words(
            session,
            project,
            audio_path=audio_path,
            audio_dir=audio_dir,
            frame_numbers=frame_numbers,
        )
    elif subs_enabled and settings.subtitle_rewhisper_on_assemble and not full_voice and words:
        if whisper_available() and audio_path.is_file():
            logger.info("[#{}] assemble: re-whisper voice_full для субтитров", project.id)
            words = await asyncio.to_thread(
                transcribe_words,
                audio_path,
                model_name=settings.whisper_model,
                language="ru",
            )
    if subs_enabled and not words:
        raise RuntimeError("Whisper не вернул слова для субтитров")

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
            ts_cells=ts_cells if not per_frame_tts else None,
            ts_row=ts_row if not per_frame_tts else None,
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
    ts_cells: list[tuple[int, str]] | None = None,
    ts_row: int | None = None,
) -> None:
    timeline_mode = "excel-r15"

    if per_frame_tts:
        audio_clips, audio_duration, time_scale, per_frame_audio = await build_assembly_timeline(
            audio_dir,
            audio_path,
            frame_numbers,
            cells=cells,
            words=words,
            per_frame_tts=True,
        )
        timeline_mode = "per-frame"
    else:
        from app.services.frame_audio import FrameAudioClip
        from app.services.montage.r15 import load_r15_markers, resolve_montage_frame_numbers

        frame_numbers = resolve_montage_frame_numbers(project, frame_numbers)
        markers, ts_row = load_r15_markers(project, frame_numbers)
        audio_duration = await probe_duration(audio_path)
        audio_clips = [
            FrameAudioClip(
                m.frame_number,
                audio_path,
                "",
                m.start_s,
                m.end_s,
                m.duration_s,
            )
            for m in markers
        ]
        ts_cells = [(m.frame_number, m.label) for m in markers]
        time_scale = 1.0
        per_frame_audio = False
        timeline_mode = MONTAGE_ENGINE_V2

    words = _scale_whisper_words(words, time_scale) if not per_frame_audio else words
    _ = frames_all, skipped_no_video, audio, whisper_art, ts_row, ts_cells

    duration_by_frame = {c.frame_number: c.duration for c in audio_clips}
    frame_timings = [
        FrameTiming(c.frame_number, c.start_ts, c.end_ts, c.duration)
        for c in audio_clips
    ]

    for fr in frames_all:
        ac = next((c for c in audio_clips if c.frame_number == fr.number), None)
        if ac is None:
            continue
        fr.start_ts = ac.start_ts
        fr.end_ts = ac.end_ts
        fr.duration_seconds = ac.duration

    out_dir = project.data_dir / "final"
    out_path = out_dir / f"{project.slug}.mp4"
    bgm = resolve_bgm(project)

    subs_path: Path | None = None
    sub_entries: list[tuple[float, float, str]] = []
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
        session.add(Artifact(
            project_id=project.id, kind=ArtifactKind.subtitle,
            uuid=uuid.uuid4().hex, path=str(subs_path),
        ))
    else:
        logger.info("[#{}] assemble: субтитры выключены в настройках сборки", project.id)

    if per_frame_tts:
        shot1_paths: dict[int, Path] = {}
        shot2_paths: dict[int, Path | None] = {}
        xlsx_path = project.data_dir / "project.xlsx"
        shot2_by = read_shot2_columns(xlsx_path) if xlsx_path.is_file() else {}
        for fr in frames:
            p1 = await _scene_video_path(session, project, fr, shot=1)
            info = shot2_by.get(fr.number)
            p2 = None
            if info is not None and info.has_shot2:
                p2 = await _scene_video_path(session, project, fr, shot=2)
            if p1 is None:
                if p2 is None:
                    p2 = await _scene_video_path(session, project, fr, shot=2)
                if p2 is None:
                    raise RuntimeError(
                        f"нет клипа shot_01/shot_02 для кадра {fr.number}"
                    )
                logger.info(
                    "[#{}] assemble: кадр {} — нет shot_01, используем shot_02",
                    project.id,
                    fr.number,
                )
                shot1_paths[fr.number] = p2
                shot2_paths[fr.number] = None
            else:
                shot1_paths[fr.number] = p1
                shot2_paths[fr.number] = p2

        clips = build_assembly_clip_specs(
            frames,
            shot1_paths,
            shot2_paths,
            duration_by_frame,
            video_trims=(montage_meta(project).get("video_trims") or None),
        )
        if subs_path is not None:
            ass_w, ass_h = await probe_video_size(clips[0].src)
            make_simple_ass(sub_entries, subs_path, width=ass_w, height=ass_h)
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
    else:
        await run_variant2(
            project,
            frame_numbers,
            audio_path,
            out_path,
            bgm=bgm,
        )
        if subs_path is not None and subs_path.is_file():
            ass_w, ass_h = await probe_video_size(out_path)
            make_simple_ass(sub_entries, subs_path, width=ass_w, height=ass_h)
            with tempfile.TemporaryDirectory(prefix="vp_subs_") as td:
                tmp = Path(td)
                tmp_ass = tmp / SUBTITLES_ASS_NAME
                shutil.copy2(subs_path, tmp_ass)
                burned = tmp / "burned.mp4"
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y", "-i", str(out_path),
                    "-vf", subtitles_vf_arg(),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "fast", "-crf", "20",
                    "-c:a", "copy",
                    "-t", f"{audio_duration:.3f}",
                    str(burned),
                    cwd=str(tmp),
                )
                await proc.communicate()
                if proc.returncode != 0:
                    raise RuntimeError("ffmpeg subtitle burn failed")
                shutil.copy2(burned, out_path)

    logger.info(
        "[#{}] assemble done: voice {:.2f}s, engine={}, timeline={}",
        project.id,
        audio_duration,
        MONTAGE_ENGINE_V2 if not per_frame_tts else "per-frame",
        timeline_mode,
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
