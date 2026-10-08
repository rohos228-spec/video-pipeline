"""Шаг 11: финальная сборка — видео и субтитры только по озвучке."""

from __future__ import annotations

import asyncio
import os
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
    HITLDecision,
    HITLKind,
    HITLRequest,
    Project,
    ProjectStatus,
)
from app.services.artifact_recovery import recover_before_assemble
from app.services.assembly import (
    SUBTITLES_ASS_NAME,
    assemble,
    make_simple_ass,
    subtitles_vf_arg,
    trim_intro_seconds,
)
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
    skip_intro_seconds_for_project,
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


async def _assemble_core(
    session: AsyncSession,
    project: Project,
    bot: Bot,
    *,
    out_path: Path,
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
        from app.services.sfx_mix import collect_sfx_inputs

        sfx_inputs = collect_sfx_inputs(project)
        if sfx_inputs:
            logger.info("[#{}] assemble: {} SFX из sfx_gen идут в микс", project.id, len(sfx_inputs))
        await assemble(
            clips,
            audio_path,
            out_path,
            subtitles_ass=subs_path,
            max_duration=audio_duration,
            tail_seconds=tail_seconds,
            bgm=bgm,
            sfx=sfx_inputs,
        )
    else:
        await run_variant2(
            project,
            frame_numbers,
            audio_path,
            out_path,
            bgm=bgm,
        )
        # ASS ещё нет на диске: путь зарезервирован выше, файл пишем здесь.
        # Старый `subs_path.is_file()` пропускал весь burn на montage-v3.
        if subs_path is not None:
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
                "[#{}] assemble: субтитры вшиты в montage-v3 ({} cues → {})",
                project.id,
                len(sub_entries),
                subs_path.name,
            )

    skip_intro = skip_intro_seconds_for_project(project)
    if skip_intro > 0:
        await trim_intro_seconds(out_path, skip_seconds=skip_intro)
        logger.info(
            "[#{}] assemble: skip intro {:.2f}s applied",
            project.id,
            skip_intro,
        )

    logger.info(
        "[#{}] assemble done: voice {:.2f}s, engine={}, timeline={}",
        project.id,
        audio_duration,
        MONTAGE_ENGINE_V2 if not per_frame_tts else "per-frame",
        timeline_mode,
    )
    await session.flush()
    return audio_duration, skip_intro


ASSEMBLE_MAX_ATTEMPTS = 2  # первая попытка + ровно один повтор


class AssembleVerificationError(RuntimeError):
    """Собранный файл не прошёл verify_artifact (статус failed или unknown)."""


def _assemble_retryable(exc: BaseException) -> bool:
    """Повторяем только сбой самой сборки или проверки итога; ошибки входа (нет клипов, озвучки) не лечатся."""
    if isinstance(exc, (AssembleVerificationError, OSError)):
        return True
    return isinstance(exc, RuntimeError) and "ffmpeg" in str(exc).lower()


# D07: independent check of the assembled file's tracks and length, on top of verify_artifact. A defect proven by the
# probe (a missing track, tracks of very different length, a length far outside the voice) fails the attempt, which the
# existing single local retry may repeat; a probe that cannot run is "unknown" and never turns a passed
# verify_artifact into a failure (verify_artifact has already refused a file that ffprobe cannot read).
ASSEMBLE_TRACK_TOLERANCE_S = 3.0      # absolute slack between the video and the audio track
ASSEMBLE_TRACK_TOLERANCE_RATIO = 0.05  # plus this share of the longer track
ASSEMBLE_MAX_OVERRUN_RATIO = 1.5       # the file may not be longer than this multiple of the voice (plus tail)
ASSEMBLE_MAX_OVERRUN_SLACK_S = 10.0


async def _probe_tracks(path: Path) -> dict | None:
    """ffprobe the file (streams and format) as JSON; None when the probe cannot run or answers nothing usable."""
    import json

    if not shutil.which("ffprobe"):
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe",
            "-show_streams", "-show_format", "-of", "json", str(path),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        if proc.returncode:
            return None
        data = json.loads(stdout)
    except (OSError, ValueError, asyncio.TimeoutError):
        return None
    return data if isinstance(data, dict) else None


def _stream_seconds(stream: dict, fallback: float | None) -> float | None:
    try:
        value = float(stream.get("duration"))
    except (TypeError, ValueError):
        return fallback
    return value if value == value and value > 0 else fallback


def check_assembly_tracks(
    probe: dict | None,
    *,
    expected_duration: float,
    tail_seconds: float = 0.0,
    size_bytes: int | None = None,
) -> dict:
    """Pure verdict on an ffprobe answer for the assembled file. ``passed`` / ``failed`` / ``unknown`` (no probe)."""
    result: dict = {"status": "unknown", "errors": [], "expected_duration": round(float(expected_duration), 3),
                    "size_bytes": size_bytes, "semantic_quality_verified": False}
    if size_bytes is not None and size_bytes <= 0:
        return {**result, "status": "failed", "errors": ["file_empty"], "reason": "file_empty"}
    if not isinstance(probe, dict) or not isinstance(probe.get("streams"), list):
        return {**result, "reason": "probe_unavailable"}
    streams = [s for s in probe["streams"] if isinstance(s, dict)]
    try:
        fmt_duration = float((probe.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        fmt_duration = None
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    errors: list[str] = []
    if not video:
        errors.append("video_track_missing")
    if not audio:
        errors.append("audio_track_missing")
    v_dur = _stream_seconds(video[0], fmt_duration) if video else None
    a_dur = _stream_seconds(audio[0], fmt_duration) if audio else None
    if video and not v_dur:
        errors.append("video_track_empty")
    if audio and not a_dur:
        errors.append("audio_track_empty")
    if v_dur and a_dur:
        slack = ASSEMBLE_TRACK_TOLERANCE_S + ASSEMBLE_TRACK_TOLERANCE_RATIO * max(v_dur, a_dur) + max(0.0, tail_seconds)
        if abs(v_dur - a_dur) > slack:
            errors.append("tracks_out_of_sync")
    if fmt_duration is not None and expected_duration > 0:
        if fmt_duration < 0.9 * expected_duration:
            errors.append("shorter_than_voice")
        elif fmt_duration > ASSEMBLE_MAX_OVERRUN_RATIO * expected_duration + max(0.0, tail_seconds) + ASSEMBLE_MAX_OVERRUN_SLACK_S:
            errors.append("longer_than_voice")
    return {**result, "status": "failed" if errors else "passed", "errors": errors,
            "reason": errors[0] if errors else None,
            "video_tracks": len(video), "audio_tracks": len(audio),
            "video_duration": None if v_dur is None else round(v_dur, 3),
            "audio_duration": None if a_dur is None else round(a_dur, 3),
            "format_duration": None if fmt_duration is None else round(fmt_duration, 3)}


async def _journal(kind: str, payload: dict) -> None:
    """Write a runtime fact to the observation journal when an attempt is open; never raises, never blocks assembly."""
    try:
        from app.services.observation import _attempt, record_fact

        if _attempt.get() is not None:
            await record_fact(kind, payload)
    except Exception:  # noqa: BLE001
        pass


async def _verify_result_tracks(work_path: Path, *, expected_duration: float, tail_seconds: float) -> dict:
    """Independent file / length / tracks check of the part file; always journals one ``artifact_verified`` fact."""
    try:
        size = work_path.stat().st_size
    except OSError:
        size = 0
    check = check_assembly_tracks(await _probe_tracks(work_path), expected_duration=expected_duration,
                                  tail_seconds=tail_seconds, size_bytes=size)
    await _journal("artifact_verified", {"stage": "assembly_result", "kind": "assembly", "local_name": work_path.name,
                                         **{k: v for k, v in check.items() if k != "semantic_quality_verified"},
                                         "semantic_quality_verified": False})
    return check


async def _keep_approve_final_after_notify_failure(
    session: AsyncSession, project: Project, sha256: str, final_path: Path, exc: BaseException
) -> HITLRequest:
    """Telegram refused the notification: the verified file stays assembled and the approve_final request is kept
    (created here when the failure happened before it existed), marked as not notified. Nothing is approved."""
    from datetime import datetime, timezone

    from app.services.hitl import create_hitl

    rows = (await session.execute(
        select(HITLRequest).where(
            HITLRequest.project_id == project.id,
            HITLRequest.kind == HITLKind.approve_final,
        ).order_by(HITLRequest.id.desc())
    )).scalars().all()
    req = next((r for r in rows if (r.payload or {}).get("sha256") == sha256
                and r.decision in (HITLDecision.pending, HITLDecision.approved)), None)
    if req is None:
        req = await create_hitl(session, project, HITLKind.approve_final,
                                payload={"step": "final", "sha256": sha256, "video_path": str(final_path)})
    req.payload = {**(req.payload or {}), "notify_failed": {
        "error_type": type(exc).__name__, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}}
    await session.flush()
    logger.warning("[#{}] assemble: Telegram не принял ролик ({}); запрос approve_final #{} сохранён без уведомления",
                   project.id, type(exc).__name__, req.id)
    await _journal("runtime_check", {"check": "уведомление в Telegram о финальном ролике", "status": "failed",
                                     "performed": True, "reason": type(exc).__name__, "hitl_request_id": req.id,
                                     "request_kept": True, "business_commit_claim": False,
                                     "semantic_quality_verified": False})
    return req


async def _approve_final_exists(session: AsyncSession, project: Project, sha256: str) -> bool:
    """Запрос approve_final для этого проекта и этого файла (по sha256) уже создан."""
    rows = (await session.execute(
        select(HITLRequest).where(
            HITLRequest.project_id == project.id,
            HITLRequest.kind == HITLKind.approve_final,
        )
    )).scalars().all()
    return any(
        (r.payload or {}).get("sha256") == sha256
        and r.decision in (HITLDecision.pending, HITLDecision.approved)
        for r in rows
    )


async def _assemble_body(
    session: AsyncSession,
    project: Project,
    bot: Bot,
    **kw,
) -> None:
    from app.services.artifact_verifier import verify_artifact

    out_dir = project.data_dir / "final"
    out_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / f"{project.slug}.mp4"
    # Остатки упавшей сборки: финальный файл они не затрагивают.
    for stale in out_dir.glob(f".{project.slug}.*.part.mp4"):
        try:
            stale.unlink()
        except OSError:
            pass
    from app.services.step_cancel import StepCancelledError

    verification: dict = {}
    # Сборка локальная (ffmpeg, без платных вызовов): один повтор при сбое сборки или проверки итога.
    # Прежний final не затрагивается, пока новый файл не прошёл verify_artifact.
    for attempt in range(1, ASSEMBLE_MAX_ATTEMPTS + 1):
        work_path = out_dir / f".{project.slug}.{uuid.uuid4().hex[:8]}.part.mp4"
        try:
            audio_duration, skip_intro = await _assemble_core(
                session, project, bot, out_path=work_path, **kw
            )
            expected = {"min_duration": max(0.5, 0.9 * (audio_duration - skip_intro))}
            verification = await verify_artifact(
                work_path, root=project.data_dir, kind="assembly", expected=expected
            )
            if verification.get("status") != "passed":
                raise AssembleVerificationError(
                    "assemble: проверка итогового видео не пройдена "
                    f"({verification.get('status')}: {verification.get('reason', '')}); "
                    "финальный файл не изменён, подтверждение не создаётся"
                )
            tail = 0.0
            try:
                tail = float(post_voiceover_tail_seconds_for_project(project) or 0.0)
            except Exception:  # noqa: BLE001
                tail = 0.0
            tracks = await _verify_result_tracks(
                work_path, expected_duration=max(0.0, audio_duration - skip_intro), tail_seconds=tail)
            if tracks.get("status") == "failed":
                raise AssembleVerificationError(
                    "assemble: независимая проверка файла и дорожек не пройдена "
                    f"({', '.join(tracks.get('errors') or [])}); "
                    "финальный файл не изменён, подтверждение не создаётся"
                )
            verification = {**verification, "result_check": {
                k: tracks.get(k) for k in ("status", "errors", "video_duration", "audio_duration", "format_duration")}}
            os.replace(work_path, final_path)
            break
        except (StepCancelledError, asyncio.CancelledError):
            raise
        except Exception as exc:  # noqa: BLE001
            if attempt >= ASSEMBLE_MAX_ATTEMPTS or not _assemble_retryable(exc):
                raise
            logger.warning(
                "[#{}] assemble: попытка {} не удалась ({}: {}); один повтор",
                project.id, attempt, type(exc).__name__, str(exc)[:200],
            )
            try:
                from app.services.observation import emit

                emit("assemble_retry", {"attempt": attempt, "error_type": type(exc).__name__,
                                        "retry_cap": ASSEMBLE_MAX_ATTEMPTS - 1})
            except Exception:  # noqa: BLE001
                pass
        finally:
            try:
                work_path.unlink()
            except OSError:
                pass

    sha256 = verification["sha256"]
    last = (await session.execute(
        select(Artifact).where(
            Artifact.project_id == project.id, Artifact.kind == ArtifactKind.final_video
        ).order_by(Artifact.id.desc()).limit(1)
    )).scalar_one_or_none()
    if last is None or (last.meta or {}).get("sha256") != sha256 or last.path != str(final_path):
        session.add(Artifact(
            project_id=project.id, kind=ArtifactKind.final_video,
            uuid=uuid.uuid4().hex, path=str(final_path),
            meta={"sha256": sha256, "verification": verification},
        ))
    project.status = ProjectStatus.assembled
    await session.flush()

    if await _approve_final_exists(session, project, sha256):
        logger.info("[#{}] assemble: approve_final для sha256 {} уже есть, не дублируем",
                    project.id, sha256[:12])
        return

    from app.services.mass_factory import on_child_montage_complete

    await on_child_montage_complete(session, project)

    try:
        await send_hitl_video(
            bot, session, project,
            kind=HITLKind.approve_final,
            video_path=str(final_path),
            caption=f"Финальный ролик #{project.id} готов. Одобрить и публиковать?",
            payload={"step": "final", "sha256": sha256},
        )
    except (StepCancelledError, asyncio.CancelledError):
        raise
    except Exception as exc:  # noqa: BLE001 - the file is verified; a notification failure must not lose the request
        await _keep_approve_final_after_notify_failure(session, project, sha256, final_path, exc)
