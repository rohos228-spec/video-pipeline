"""Прогон методики разбора речи → R15 → (опционально) assemble.

Фазы короткие: speech без DB → Excel R15 → короткий flush кадров с retry.
Не держим AsyncSession открытой на NeMo/ffmpeg (иначе SQLite locked).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import Artifact, ArtifactKind, Frame, Project, ProjectStatus
from app.services.audio_align_methods import (
    resolve_align_method,
    run_speech_align,
)
from app.services.frame_audio import (
    FrameAudioClip,
    find_voice_full_on_disk,
    _voiceover_cells_for_frames,
)
from app.services.media_probe import probe_duration
from app.services.project_state import compute_actual_status
from app.services.reset_step import reset_step
from app.services.step_cancel import raise_if_cancelled
from app.services.whisper import dump_words_json, load_words_json


def _is_sqlite_locked(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "database is locked" in msg or "database is busy" in msg


async def _latest_words_artifact(
    session: AsyncSession, project_id: int, *, method_id: str
) -> Artifact | None:
    rows = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.kind == ArtifactKind.whisper_words,
            )
            .order_by(Artifact.id.desc())
            .limit(12)
        )
    ).scalars().all()
    for art in rows:
        meta = art.meta if isinstance(art.meta, dict) else {}
        if meta.get("align_method") == method_id and meta.get("engine") == "nemo":
            if art.path and Path(art.path).is_file():
                return art
    return None


async def _load_align_inputs(
    session: AsyncSession,
    project: Project,
    *,
    method_id: str,
    force_asr: bool,
) -> dict[str, Any]:
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number.asc())
        )
    ).scalars().all()
    if not frames:
        raise RuntimeError("нет кадров в БД")

    from app.storage.plan_sheet_v8 import read_plan_voiceover_cells

    try:
        cells = read_plan_voiceover_cells(project, [f.number for f in frames])
    except Exception:  # noqa: BLE001
        cells = [(f.number, (f.voiceover_text or "")) for f in frames]
    cells = _voiceover_cells_for_frames(project, frames, cells)
    if not any(t.strip() for _, t in cells):
        raise RuntimeError("нет текста R49 / voiceover для align")

    voice_path = find_voice_full_on_disk(
        project.data_dir,
        meta=project.meta if isinstance(project.meta, dict) else None,
    )
    if voice_path is None or not voice_path.is_file():
        raise RuntimeError("нет voice_full на диске")

    cached_words = None
    if method_id != "silence" and not force_asr:
        art = await _latest_words_artifact(session, project.id, method_id=method_id)
        if art and art.path:
            cached_words = load_words_json(Path(art.path)) or None

    return {
        "frame_numbers": [f.number for f in frames],
        "cells": cells,
        "voice_path": voice_path,
        "cached_words": cached_words,
        "data_dir": project.data_dir,
        "slug": project.slug,
    }


async def _persist_align_db(
    session: AsyncSession,
    project: Project,
    *,
    method_id: str,
    clips: list[FrameAudioClip],
    words_path: Path | None,
    speech_source: str,
    crumbs: int,
    master_s: float,
    r15_written: int,
    engine: str,
) -> None:
    """Короткий write: bulk UPDATE кадров + meta + artifact."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for clip in clips:
        await session.execute(
            update(Frame)
            .where(
                Frame.project_id == project.id,
                Frame.number == clip.frame_number,
            )
            .values(
                start_ts=float(clip.start_ts),
                end_ts=float(clip.end_ts),
                updated_at=now,
            )
        )

    if words_path is not None:
        session.add(
            Artifact(
                project_id=project.id,
                kind=ArtifactKind.whisper_words,
                uuid=uuid.uuid4().hex,
                path=str(words_path),
                meta={
                    "source": "audio_align",
                    "align_method": method_id,
                    "engine": "nemo",
                },
            )
        )

    meta = dict(project.meta or {}) if isinstance(project.meta, dict) else {}
    meta["audio_align_last"] = {
        "method": method_id,
        "crumbs": crumbs,
        "words_source": speech_source,
        "engine": engine,
        "master_s": master_s,
        "r15_written": r15_written,
    }
    project.meta = meta
    await session.flush()


async def _persist_align_db_with_retry(
    project_id: int,
    *,
    method_id: str,
    clips: list[FrameAudioClip],
    words_path: Path | None,
    speech_source: str,
    crumbs: int,
    master_s: float,
    r15_written: int,
    engine: str,
) -> None:
    last: BaseException | None = None
    for attempt in range(1, 10):
        try:
            async with session_scope() as session:
                project = await session.get(Project, project_id)
                if project is None:
                    raise RuntimeError(f"проект #{project_id} не найден")
                await _persist_align_db(
                    session,
                    project,
                    method_id=method_id,
                    clips=clips,
                    words_path=words_path,
                    speech_source=speech_source,
                    crumbs=crumbs,
                    master_s=master_s,
                    r15_written=r15_written,
                    engine=engine,
                )
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            if _is_sqlite_locked(exc) and attempt < 9:
                wait = min(1.5 * attempt, 12.0)
                logger.warning(
                    "audio_align #{} DB locked при записи кадров ({}/9), wait {:.1f}s",
                    project_id,
                    attempt,
                    wait,
                )
                await asyncio.sleep(wait)
                continue
            raise
    assert last is not None
    raise last


async def run_audio_align_for_project(
    project_id: int,
    *,
    method: str,
    force_asr: bool = False,
    run_assemble: bool = True,
    bot: Any = None,
) -> dict[str, Any]:
    """Полный цикл без долгой сессии на speech."""
    raise_if_cancelled(project_id)
    method_id = resolve_align_method(method)
    summary: dict[str, Any] = {
        "project_id": project_id,
        "method": method_id,
        "force_asr": bool(force_asr),
        "run_assemble": bool(run_assemble),
        "engine": "nemo" if method_id != "silence" else "ffmpeg_silence",
    }

    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            summary["error"] = "проект не найден"
            return summary
        try:
            inputs = await _load_align_inputs(
                session, project, method_id=method_id, force_asr=force_asr
            )
        except Exception as exc:  # noqa: BLE001
            summary["error"] = str(exc)
            return summary

    voice_path: Path = inputs["voice_path"]
    cells = inputs["cells"]
    summary["voice_file"] = str(voice_path)

    master = await probe_duration(voice_path)
    summary["master_s"] = round(master, 3)

    raise_if_cancelled(project_id)
    try:
        result = await asyncio.to_thread(
            run_speech_align,
            method_id,
            voice_path,
            cells,
            master,
            cached_words=inputs["cached_words"],
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("[#{}] audio_align speech failed ({})", project_id, method_id)
        summary["error"] = str(exc)
        return summary

    summary["words_source"] = result.speech_source
    summary["words_n"] = len(result.words)

    text_by = dict(cells)
    clips = [
        FrameAudioClip(
            frame_number=t.frame_number,
            path=voice_path,
            text=text_by.get(t.frame_number, ""),
            start_ts=t.start_ts,
            end_ts=t.end_ts,
            duration=t.duration,
        )
        for t in result.timings
    ]
    crumbs = sum(1 for c in clips if c.duration <= 0.1 + 1e-9)
    summary["crumbs"] = crumbs
    summary["clips_n"] = len(clips)

    # Excel R15 — источник правды для доски; пишем ДО DB, чтобы lock не съел результат.
    from app.services.plan_timestamps import write_asr_timestamps_to_r15

    # Нужен Project только для data_dir/xlsx — лёгкий stub через session.
    async with session_scope() as session:
        project = await session.get(Project, project_id)
        if project is None:
            summary["error"] = "проект не найден"
            return summary
        written = write_asr_timestamps_to_r15(project, clips, allow_crumbs=True)
    summary["r15_written"] = written
    if written <= 0:
        summary["error"] = "не удалось записать R15 (закрой Excel?)"
        return summary

    words_path: Path | None = None
    if result.words and result.speech_source != "cache":
        audio_dir = Path(inputs["data_dir"]) / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        words_path = audio_dir / f"words_{method_id}_{uuid.uuid4().hex[:8]}.json"
        dump_words_json(result.words, words_path)

    try:
        await _persist_align_db_with_retry(
            project_id,
            method_id=method_id,
            clips=clips,
            words_path=words_path,
            speech_source=result.speech_source,
            crumbs=crumbs,
            master_s=summary["master_s"],
            r15_written=written,
            engine=summary["engine"],
        )
    except Exception as exc:  # noqa: BLE001
        # R15 уже записан — доска подхватит; DB frames вторичны.
        logger.warning(
            "[#{}] audio_align: R15 ok ({}), но DB frames не обновились: {}",
            project_id,
            written,
            exc,
        )
        summary["db_frames_error"] = str(exc)
        if _is_sqlite_locked(exc):
            summary["db_frames_error"] = (
                "database is locked — R15 записана, кадры в БД обновятся при следующем sync"
            )

    raise_if_cancelled(project_id)
    if not run_assemble:
        summary["done"] = True
        summary["next"] = "R15 обновлена — запустите «Монтаж» или assemble"
        logger.info("[#{}] audio_align: R15 ok ({}), assemble пропущен", project_id, written)
        return summary

    logger.info(
        "[#{}] audio_align: R15 записана ({}), запускаем assemble…",
        project_id,
        written,
    )
    if bot is None:
        from app.telegram.noop_bot import get_worker_bot

        bot = get_worker_bot(None)

    from app.orchestrator.steps import assemble as assemble_mod

    try:
        async with session_scope() as session:
            project = await session.get(Project, project_id)
            if project is None:
                summary["error"] = "проект не найден"
                return summary
            reset_info = await reset_step(session, project, "assemble")
            summary["assemble_reset"] = reset_info
            project.status = ProjectStatus.assembling
            await session.flush()
            await assemble_mod.run(session, project, bot)
            summary["final_status"] = project.status.value
            if project.status is ProjectStatus.assembled:
                summary["done"] = True
                out = project.data_dir / "final" / f"{project.slug}.mp4"
                summary["final_video"] = str(out) if out.is_file() else None
                logger.info(
                    "[#{}] audio_align: assemble done → {}",
                    project_id,
                    summary.get("final_video"),
                )
            else:
                summary["error"] = f"сборка не завершилась: status={project.status.value}"
            actual = await compute_actual_status(session, project)
            if project.status != actual:
                project.status = actual
    except Exception as exc:  # noqa: BLE001
        logger.exception("[#{}] audio_align: assemble failed", project_id)
        summary["error"] = str(exc)
        async with session_scope() as session:
            project = await session.get(Project, project_id)
            if project is not None:
                actual = await compute_actual_status(session, project)
                if project.status != actual:
                    project.status = actual
                summary["final_status"] = project.status.value
        return summary

    return summary


async def run_audio_align(
    session: AsyncSession,
    project: Project,
    *,
    method: str,
    force_asr: bool = False,
    run_assemble: bool = True,
    bot: Any = None,
) -> dict[str, Any]:
    """Совместимость: игнорирует переданную session, ведёт короткие фазы сам."""
    _ = session  # не держим чужую долгую сессию
    return await run_audio_align_for_project(
        project.id,
        method=method,
        force_asr=force_asr,
        run_assemble=run_assemble,
        bot=bot,
    )
