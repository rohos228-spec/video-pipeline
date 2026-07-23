"""Монтаж shot_02: вторая половина сцены (видео) внутри того же сегмента озвучки."""

from __future__ import annotations

import re
from pathlib import Path

from loguru import logger

from app.models import Project
from app.services.assembly import ClipSpec
from app.services.plan_shot2 import read_shot2_columns

_S2_IN_NAME = re.compile(r"(^|_)s2(_|$)", re.I)


def shot2_frame_numbers(project: Project) -> set[int]:
    """Кадры с вторым клипом на диске (clip_NNN_s2_*.mp4 или пара без _s2_)."""
    videos_dir = project.data_dir / "videos"
    if not videos_dir.is_dir():
        return set()
    out: set[int] = set()
    seen: set[int] = set()
    for path in videos_dir.glob("clip_*.mp4"):
        m = re.match(r"clip_(\d+)_", path.name, re.I)
        if not m:
            continue
        num = int(m.group(1))
        if num in seen:
            continue
        _, disk2 = find_scene_clips(videos_dir, num)
        if disk2 is not None and disk2.is_file():
            out.add(num)
        seen.add(num)
    return out


def shot2_xlsx_frame_numbers(project: Project) -> set[int]:
    """Кадры, где в xlsx заполнен блок shot_02 (для генерации / отчётов)."""
    xlsx = project.data_dir / "project.xlsx"
    if not xlsx.is_file():
        return set()
    by = read_shot2_columns(xlsx)
    return {n for n, info in by.items() if info.has_shot2}


def _is_shot2_clip_name(name: str) -> bool:
    return bool(_S2_IN_NAME.search(name))


def find_scene_clips(videos_dir: Path, frame_number: int) -> tuple[Path | None, Path | None]:
    """(shot_01, shot_02) — последние по mtime среди подходящих имён."""
    if not videos_dir.is_dir():
        return None, None
    all_clips = sorted(
        (p for p in videos_dir.glob(f"clip_{frame_number:03d}_*.mp4") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
    )
    if not all_clips:
        return None, None
    shot2 = [p for p in all_clips if _is_shot2_clip_name(p.name)]
    shot1 = [p for p in all_clips if not _is_shot2_clip_name(p.name)]
    if shot1 and shot2:
        return shot1[-1], shot2[-1]
    if len(shot1) >= 2:
        return shot1[0], shot1[-1]
    if shot1:
        return shot1[-1], None
    if len(shot2) >= 2:
        return shot2[0], shot2[-1]
    if shot2:
        return shot2[-1], None
    return all_clips[-1], None


def _append_scene_clips(
    specs: list[ClipSpec],
    *,
    project: Project,
    frame_number: int,
    segment_duration: float,
    shot1: Path,
    disk2: Path | None,
    shot2_nums: set[int],
    timeline_start: float,
    timeline_end: float,
) -> Path:
    """Добавить shot_01 (+ shot_02 пополам) на segment_duration; вернуть последний src."""
    if segment_duration <= 0:
        raise RuntimeError(f"кадр {frame_number}: нулевая длительность сегмента")

    if frame_number in shot2_nums and disk2 is not None and disk2.is_file():
        half = segment_duration / 2.0
        mid = timeline_start + half
        specs.append(
            ClipSpec(
                src=shot1,
                duration=half,
                frame_number=frame_number,
                timeline_start=timeline_start,
                timeline_end=mid,
                kind="shot2",
            )
        )
        specs.append(
            ClipSpec(
                src=disk2,
                duration=half,
                frame_number=frame_number,
                timeline_start=mid,
                timeline_end=timeline_end,
                kind="shot2",
            )
        )
        logger.debug(
            "[#{}] shot_02 frame {}: {:.2f}s → {} + {} ({:.2f}s each)",
            project.id,
            frame_number,
            segment_duration,
            shot1.name,
            disk2.name,
            half,
        )
        return disk2

    specs.append(
        ClipSpec(
            src=shot1,
            duration=segment_duration,
            frame_number=frame_number,
            timeline_start=timeline_start,
            timeline_end=timeline_end,
            kind="scene",
        )
    )
    return shot1


def build_video_clip_specs(
    project: Project,
    *,
    frames: list,
    audio_clips: list,
    primary_paths: dict[int, Path],
    voice_duration: float | None = None,
) -> list[ClipSpec]:
    """Абсолютный монтаж: каждый кадр только в своём окне [start_ts, end_ts]."""
    shot2_nums = shot2_frame_numbers(project)
    xlsx_shot2 = shot2_xlsx_frame_numbers(project)
    videos_dir = project.data_dir / "videos"
    specs: list[ClipSpec] = []
    split_count = 0
    xlsx_only: list[int] = []

    clip_by_frame = {c.frame_number: c for c in audio_clips}
    prev_end = -0.01

    for fr in frames:
        num = fr.number
        ac = clip_by_frame.get(num)
        if ac is None:
            raise RuntimeError(f"нет метки R15 для кадра {num}")

        if ac.duration <= 0 or ac.end_ts <= ac.start_ts:
            raise RuntimeError(
                f"кадр {num}: битая метка {ac.start_ts:.2f}–{ac.end_ts:.2f}s"
            )

        if ac.start_ts < prev_end - 0.02:
            raise RuntimeError(
                f"кадр {num}: start {ac.start_ts:.2f}s перекрывает предыдущий "
                f"(конец ~{prev_end:.2f}s)"
            )

        disk1, disk2 = find_scene_clips(videos_dir, num)
        shot1 = primary_paths.get(num) or disk1
        if shot1 is None or not shot1.is_file():
            raise RuntimeError(f"нет клипа shot_01 для кадра {num}")

        segment = ac.end_ts - ac.start_ts
        if segment > 45.0:
            raise RuntimeError(
                f"кадр {num}: сегмент {segment:.1f}s — битая метка R15 "
                f"({ac.start_ts:.2f}–{ac.end_ts:.2f})"
            )

        last_src = _append_scene_clips(
            specs,
            project=project,
            frame_number=num,
            segment_duration=segment,
            shot1=shot1,
            disk2=disk2,
            shot2_nums=shot2_nums,
            timeline_start=ac.start_ts,
            timeline_end=ac.end_ts,
        )
        logger.info(
            "[#{}] ABS frame {}: {:.2f}–{:.2f}s ({:.2f}s) → {}",
            project.id,
            num,
            ac.start_ts,
            ac.end_ts,
            segment,
            shot1.name,
        )
        if num in shot2_nums and disk2 is not None and disk2.is_file():
            split_count += 1
        elif num in xlsx_shot2 and num not in shot2_nums:
            xlsx_only.append(num)

        prev_end = max(prev_end, ac.end_ts)

    if xlsx_only:
        sample = ", ".join(str(n) for n in xlsx_only[:8])
        extra = f" (+{len(xlsx_only) - 8})" if len(xlsx_only) > 8 else ""
        logger.info(
            "[#{}] shot_02: в xlsx {} кадров, на диске s2 — {}. "
            "Без clip_*_s2_* монтируем только shot_01 (пример пропуска: {}{})",
            project.id,
            len(xlsx_shot2),
            split_count,
            sample,
            extra,
        )

    if split_count:
        logger.info(
            "[#{}] assemble shot_02: {} сцен с двумя клипами (50/50 по озвучке)",
            project.id,
            split_count,
        )

    return specs
