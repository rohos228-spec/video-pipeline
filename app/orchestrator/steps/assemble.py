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
from app.services.artifact_recovery import ensure_whisper_words, recover_before_assemble
from app.services.assembly import assemble, make_simple_ass, subtitles_vf_arg, SUBTITLES_ASS_NAME
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
from app.services.montage_board_meta import montage_meta
from app.services.plan_shot2 import read_shot2_columns
from app.storage.plan_sheet_v8 import read_plan_voiceover_cells


async def _scene_video_path(
    session: AsyncSession,
    project: Project,
    frame: Frame,
    *,
    shot: int = 1,
) -> Path | None:
    """Artifact или newest-on-disk — кто свежее (mtime), тот и в монтаж.

    Иначе после regen без finalize UI показывает новый clip_*, а сборка
    продолжает mux'ить stale Artifact.
    """
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
    # Unique by resolve, pick newest mtime.
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
    logger.info("[#{}] assemble starting", project.id)

    await recover_before_assemble(session, project)

    from app.services.frame_timeline_sync import sync_frame_timestamps_if_needed

    try:
        await sync_frame_timestamps_if_needed(session, project)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[#{}] assemble: frame_timeline_sync: {}", project.id, exc)

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
        # Нужен shot_01 или хотя бы shot_02 (fallback, если первого нет).
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

    cells_preview = _voiceover_cells_for_frames(
        project,
        frames_all,
        read_plan_voiceover_cells(project, [fr.number for fr in frames_all]),
    )
    cell_text = {n: (t or "").strip() for n, t in cells_preview}
    from app.services.frame_timeline_sync import is_placeholder_voiceover

    frames_with_vo: list[Frame] = []
    skipped_no_voiceover: list[int] = []
    for fr in frames:
        text = cell_text.get(fr.number, "")
        if text and not is_placeholder_voiceover(text):
            frames_with_vo.append(fr)
        else:
            skipped_no_voiceover.append(fr.number)
    if skipped_no_voiceover:
        logger.warning(
            "[#{}] assemble: кадры {} без текста R49 — не входят в таймлайн",
            project.id,
            skipped_no_voiceover[:30],
        )
    frames = frames_with_vo
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

    from app.services.frame_timeline_sync import timeline_frames_and_cells

    _timeline_frames, align_cells = timeline_frames_and_cells(project, frames_all)
    align_nums = [n for n, _ in align_cells]

    ts_cells: list[tuple[int, str]] | None = None
    ts_row: int | None = None
    if not per_frame_tts:
        from app.services.montage.r15 import resolve_montage_frame_numbers
        from app.services.plan_timestamps import (
            count_parsed_timestamp_cells,
            ensure_r15_from_asr,
        )
        from app.services.montage_asr import ensure_montage_words

        montage_frame_numbers = resolve_montage_frame_numbers(project, align_nums)
        montage_cells = [
            (n, text) for n, text in align_cells if n in set(montage_frame_numbers)
        ]
        if len(montage_cells) < len(montage_frame_numbers):
            by_num = dict(align_cells)
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
            xlsx_path = project.data_dir / "project.xlsx"
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
            project.data_dir / "project.xlsx",
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
            align_nums=align_nums,
            per_frame_tts=per_frame_tts,
            subs_enabled=subs_enabled,
            words=words,
            whisper_art=whisper_art,
            cells=cells,
            align_cells=align_cells,
            ts_cells=ts_cells,
            ts_row=ts_row,
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
    align_nums: list[int],
    per_frame_tts: bool,
    subs_enabled: bool,
    words: list[WordTS],
    whisper_art,
    cells: list[tuple[int, str]],
    align_cells: list[tuple[int, str]],
    ts_cells: list[tuple[int, str]] | None = None,
    ts_row: int | None = None,
) -> None:
    from app.services.montage.variant2 import MONTAGE_ENGINE_V2, run_variant2
    from app.services.montage.r15 import load_r15_markers, resolve_montage_frame_numbers

    timeline_mode = "legacy-stretch"
    per_frame_audio = False
    time_scale = 1.0

    if per_frame_tts:
        try:
            audio_clips, audio_duration, time_scale, per_frame_audio = await build_assembly_timeline(
                audio_dir,
                audio_path,
                frame_numbers,
                cells=cells,
                align_cells=align_cells,
                words=words,
                per_frame_tts=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(str(exc)) from exc
        timeline_mode = "per-frame"
    else:
        montage_frame_numbers = resolve_montage_frame_numbers(project, align_nums)
        markers, ts_row = load_r15_markers(project, montage_frame_numbers)
        audio_duration = await probe_duration(audio_path)
        from app.services.frame_audio import FrameAudioClip

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
    frame_timings = [
        FrameTiming(c.frame_number, c.start_ts, c.end_ts, c.duration)
        for c in audio_clips
    ]
    duration_by_frame = {c.frame_number: c.duration for c in audio_clips}

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
            # Нет shot_01 — нормально: подставляем shot_02 на весь слот.
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
        montage_frame_numbers = resolve_montage_frame_numbers(project, align_nums)
        await run_variant2(
            project,
            montage_frame_numbers,
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
