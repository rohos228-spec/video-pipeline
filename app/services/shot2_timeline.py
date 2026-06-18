"""Таймлайн монтажа: shot_01 + shot_02 внутри окна закадровки кадра."""

from __future__ import annotations

from pathlib import Path

from app.models import Frame
from app.services.assembly import ClipSpec


def split_voiceover_duration(total: float) -> tuple[float, float]:
    """Делит длительность кадра пополам: shot_01, затем shot_02."""
    total = max(float(total), 0.01)
    first = round(total / 2, 3)
    second = round(total - first, 3)
    return first, second


def build_assembly_clip_specs(
    frames: list[Frame],
    shot1_paths: dict[int, Path],
    shot2_paths: dict[int, Path | None],
    duration_by_frame: dict[int, float],
) -> list[ClipSpec]:
    """Клипы в порядке воспроизведения: для сцены с shot_02 — два клипа на одно окно R49."""
    clips: list[ClipSpec] = []
    for fr in frames:
        total = duration_by_frame.get(fr.number)
        if total is None:
            raise RuntimeError(
                f"нет таймлайна аудио для кадра {fr.number} — перезапустите «Аудио»"
            )
        p1 = shot1_paths.get(fr.number)
        if p1 is None or not p1.is_file():
            raise RuntimeError(f"нет клипа shot_01 для кадра {fr.number}")
        p2 = shot2_paths.get(fr.number)
        if p2 is not None and p2.is_file():
            d1, d2 = split_voiceover_duration(total)
            clips.append(ClipSpec(src=p1, duration=d1))
            clips.append(ClipSpec(src=p2, duration=d2))
        else:
            clips.append(ClipSpec(src=p1, duration=total))
    return clips
