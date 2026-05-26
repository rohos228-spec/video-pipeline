"""Подгонка длительности видеоклипа под таймкод Whisper (обрезка / замедление ≤15%)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClipFitPlan:
    output_duration: float
    mode: str  # use_source | stretch | trim


def plan_clip_fit(
    source_duration: float,
    target_duration: float,
    *,
    max_stretch_ratio: float = 0.15,
) -> ClipFitPlan:
    """source — длина исходного mp4, target — слот из Whisper.

    - Короче слота: используем всю длину клипа (слот «обрезается» по факту).
    - Длиннее слота: замедляем до target, если source/target ≤ 1+max_stretch; иначе обрезка.
    """
    if target_duration <= 0:
        return ClipFitPlan(0.0, "trim")
    if source_duration <= 0:
        return ClipFitPlan(target_duration, "trim")
    if source_duration <= target_duration:
        return ClipFitPlan(source_duration, "use_source")
    ratio = source_duration / target_duration
    if ratio <= 1.0 + max_stretch_ratio:
        return ClipFitPlan(target_duration, "stretch")
    return ClipFitPlan(target_duration, "trim")
