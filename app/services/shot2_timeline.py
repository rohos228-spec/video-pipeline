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
    *,
    video_trims: dict[str, dict[str, float]] | None = None,
) -> list[ClipSpec]:
    """Клипы в порядке воспроизведения: для сцены с shot_02 — два клипа на одно окно R49."""
    trims = video_trims or {}
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
            t1 = trims.get(f"{fr.number}:1") or {}
            t2 = trims.get(f"{fr.number}:2") or {}
            clips.append(
                ClipSpec(
                    src=p1,
                    duration=d1,
                    trim_start=float(t1.get("start", 0.0)),
                    trim_end=float(t1["end"]) if "end" in t1 else None,
                )
            )
            clips.append(
                ClipSpec(
                    src=p2,
                    duration=d2,
                    trim_start=float(t2.get("start", 0.0)),
                    trim_end=float(t2["end"]) if "end" in t2 else None,
                )
            )
        else:
            t1 = trims.get(f"{fr.number}:1") or {}
            clips.append(
                ClipSpec(
                    src=p1,
                    duration=total,
                    trim_start=float(t1.get("start", 0.0)),
                    trim_end=float(t1["end"]) if "end" in t1 else None,
                )
            )
    return clips
