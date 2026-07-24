"""Прогон методики разбора аудио → R15 → (опционально) assemble."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Frame, Project, ProjectStatus
from app.services.audio_align_methods import apply_align_method, resolve_align_method
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
from app.settings import settings


async def _latest_words_artifact(
    session: AsyncSession, project_id: int
) -> Artifact | None:
    return (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.kind == ArtifactKind.whisper_words,
            )
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def run_audio_align(
    session: AsyncSession,
    project: Project,
    *,
    method: str,
    force_asr: bool = False,
    run_assemble: bool = True,
    bot: Any = None,
) -> dict[str, Any]:
    """Раскладка слов → Frame + R15 выбранной методикой; опционально сборка."""
    raise_if_cancelled(project.id)
    method_id = resolve_align_method(method)
    summary: dict[str, Any] = {
        "project_id": project.id,
        "method": method_id,
        "force_asr": bool(force_asr),
        "run_assemble": bool(run_assemble),
    }

    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number.asc())
        )
    ).scalars().all()
    if not frames:
        summary["error"] = "нет кадров в БД"
        return summary

    from app.storage.plan_sheet_v8 import read_plan_voiceover_cells

    try:
        cells = read_plan_voiceover_cells(project, [f.number for f in frames])
    except Exception:  # noqa: BLE001
        cells = [(f.number, (f.voiceover_text or "")) for f in frames]
    cells = _voiceover_cells_for_frames(project, frames, cells)
    if not any(t.strip() for _, t in cells):
        summary["error"] = "нет текста R49 / voiceover для align"
        return summary

    voice_path = find_voice_full_on_disk(
        project.data_dir,
        meta=project.meta if isinstance(project.meta, dict) else None,
    )
    if voice_path is None or not voice_path.is_file():
        summary["error"] = "нет voice_full на диске"
        return summary
    summary["voice_file"] = str(voice_path)

    master = await probe_duration(voice_path)
    summary["master_s"] = round(master, 3)

    audio_dir = project.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    words = []
    words_source = "none"
    if not force_asr:
        art = await _latest_words_artifact(session, project.id)
        if art and art.path and Path(art.path).is_file():
            words = load_words_json(Path(art.path))
            if words:
                words_source = "words.json"
    if not words:
        from app.services.asr import active_asr_backend, transcribe_words
        import asyncio

        logger.info(
            "[#{}] audio_align: ASR {} по {:.2f}s …",
            project.id,
            active_asr_backend(),
            master,
        )
        words = await asyncio.to_thread(
            transcribe_words,
            voice_path,
            model_name=settings.whisper_model,
            language="ru",
            beam_size=1 if master > 300 else 5,
        )
        words_source = active_asr_backend()
        if not words:
            summary["error"] = "ASR не вернул слова"
            return summary
        words_path = audio_dir / f"words_{uuid.uuid4().hex[:8]}.json"
        dump_words_json(words, words_path)
        session.add(
            Artifact(
                project_id=project.id,
                kind=ArtifactKind.whisper_words,
                uuid=uuid.uuid4().hex,
                path=str(words_path),
                meta={"source": "audio_align", "method": method_id},
            )
        )
        await session.flush()

    summary["words_source"] = words_source
    summary["words_n"] = len(words)

    timings = apply_align_method(method_id, cells, words, master)
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
        for t in timings
    ]
    crumbs = sum(1 for c in clips if c.duration <= 0.1 + 1e-9)
    summary["crumbs"] = crumbs
    summary["clips_n"] = len(clips)

    by_num = {f.number: f for f in frames}
    for clip in clips:
        fr = by_num.get(clip.frame_number)
        if fr is None:
            continue
        fr.start_ts = clip.start_ts
        fr.end_ts = clip.end_ts
    await session.flush()

    from app.services.plan_timestamps import write_asr_timestamps_to_r15

    written = write_asr_timestamps_to_r15(project, clips, allow_crumbs=True)
    summary["r15_written"] = written
    if written <= 0:
        summary["error"] = "не удалось записать R15 (закрой Excel?)"
        return summary

    meta = dict(project.meta or {}) if isinstance(project.meta, dict) else {}
    meta["audio_align_last"] = {
        "method": method_id,
        "crumbs": crumbs,
        "words_source": words_source,
        "master_s": summary["master_s"],
        "r15_written": written,
    }
    project.meta = meta
    await session.flush()

    raise_if_cancelled(project.id)
    if not run_assemble:
        summary["done"] = True
        summary["next"] = "R15 обновлена — запустите «Монтаж» или assemble"
        return summary

    reset_info = await reset_step(session, project, "assemble")
    summary["assemble_reset"] = reset_info

    if bot is None:
        from app.telegram.noop_bot import get_worker_bot

        bot = get_worker_bot(None)

    from app.orchestrator.steps import assemble as assemble_mod

    project.status = ProjectStatus.assembling
    await session.flush()
    try:
        await assemble_mod.run(session, project, bot)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[#{}] audio_align: assemble failed", project.id)
        summary["error"] = str(exc)
        actual = await compute_actual_status(session, project)
        if project.status != actual:
            project.status = actual
            await session.flush()
        summary["final_status"] = project.status.value
        return summary

    summary["final_status"] = project.status.value
    if project.status is ProjectStatus.assembled:
        summary["done"] = True
        out = project.data_dir / "final" / f"{project.slug}.mp4"
        summary["final_video"] = str(out) if out.is_file() else None
    else:
        summary["error"] = f"сборка не завершилась: status={project.status.value}"

    actual = await compute_actual_status(session, project)
    if project.status != actual:
        project.status = actual
        await session.flush()
    return summary
