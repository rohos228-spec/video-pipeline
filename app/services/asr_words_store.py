"""Persist word-level ASR into SQLite ``asr_words`` (replace-per-project)."""

from __future__ import annotations

import uuid
from typing import Any

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AsrWord, Frame
from app.services.whisper import WordTS


def _frame_number_for_word(
    mid_s: float,
    segments: list[dict[str, Any]] | None,
) -> int | None:
    if not segments:
        return None
    for seg in segments:
        try:
            start = float(seg.get("start_ts"))
            end = float(seg.get("end_ts"))
            num = int(seg.get("frame_number"))
        except (TypeError, ValueError):
            continue
        # Последний сегмент: включаем правую границу.
        if start <= mid_s < end or (mid_s == end and seg is segments[-1]):
            return num
    # Fallback: ближайший сегмент по центру
    best_n: int | None = None
    best_d = 1e18
    for seg in segments:
        try:
            start = float(seg.get("start_ts"))
            end = float(seg.get("end_ts"))
            num = int(seg.get("frame_number"))
        except (TypeError, ValueError):
            continue
        center = (start + end) / 2.0
        d = abs(mid_s - center)
        if d < best_d:
            best_d = d
            best_n = num
    return best_n


async def replace_project_asr_words(
    session: AsyncSession,
    project_id: int,
    words: list[WordTS],
    *,
    backend: str = "",
    run_uuid: str | None = None,
    artifact_uuid: str | None = None,
    frame_segments: list[dict[str, Any]] | None = None,
) -> str:
    """Заменить все слова проекта новым ASR-прогоном. Возвращает ``run_uuid``."""
    rid = (run_uuid or uuid.uuid4().hex).strip()
    await session.execute(delete(AsrWord).where(AsrWord.project_id == project_id))

    frame_ids: dict[int, int] = {}
    if frame_segments:
        nums = {
            int(s["frame_number"])
            for s in frame_segments
            if s.get("frame_number") is not None
        }
        if nums:
            rows = (
                await session.execute(
                    select(Frame.id, Frame.number).where(
                        Frame.project_id == project_id,
                        Frame.number.in_(nums),
                    )
                )
            ).all()
            frame_ids = {int(n): int(i) for i, n in rows}

    batch: list[AsrWord] = []
    for idx, w in enumerate(words):
        mid = (float(w.start) + float(w.end)) / 2.0
        fnum = _frame_number_for_word(mid, frame_segments)
        batch.append(
            AsrWord(
                project_id=project_id,
                run_uuid=rid,
                idx=idx,
                word=(w.word or "").strip() or " ",
                start_s=float(w.start),
                end_s=float(w.end),
                prob=float(getattr(w, "prob", 0.0) or 0.0),
                frame_number=fnum,
                frame_id=frame_ids.get(fnum) if fnum is not None else None,
                backend=(backend or "")[:64],
                artifact_uuid=artifact_uuid,
            )
        )
    if batch:
        session.add_all(batch)
    await session.flush()
    logger.info(
        "[#{}] asr_words: записано {} слов run={} backend={}",
        project_id,
        len(batch),
        rid[:12],
        backend or "—",
    )
    return rid


async def load_project_asr_words(
    session: AsyncSession,
    project_id: int,
) -> list[AsrWord]:
    result = await session.execute(
        select(AsrWord)
        .where(AsrWord.project_id == project_id)
        .order_by(AsrWord.idx.asc())
    )
    return list(result.scalars().all())


def asr_words_to_dicts(rows: list[AsrWord]) -> list[dict[str, Any]]:
    return [
        {
            "idx": r.idx,
            "word": r.word,
            "start": r.start_s,
            "end": r.end_s,
            "prob": r.prob,
            "frame_number": r.frame_number,
            "frame_id": r.frame_id,
            "run_uuid": r.run_uuid,
            "backend": r.backend,
        }
        for r in rows
    ]
