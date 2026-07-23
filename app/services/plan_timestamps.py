"""Таймкоды кадров на листе «план» (строка 15): ASR → «0:03.28-0:05.76»."""

from __future__ import annotations

import re

from loguru import logger

from app.services.media_probe import probe_duration
from app.storage.plan_sheet_v8 import read_plan_timestamps_cells, write_plan_timestamps

# 0:03.28-0:05.76  (минуты : секунды.сотые)
_TIMECODE_RE = re.compile(r"^(\d+):(\d{2})\.(\d{2})$")
_TIMECODE_LEGACY_RE = re.compile(r"^(\d+):(\d{2})$")
_DASHES = str.maketrans("–—−", "---")


def format_timecode(seconds: float) -> str:
    """Секунды → M:SS.ss без округления до целых."""
    total = max(0.0, float(seconds))
    minutes = int(total // 60)
    sec = total - minutes * 60
    return f"{minutes}:{sec:05.2f}"


def format_timecode_range(start: float, end: float) -> str:
    end = max(float(end), float(start) + 0.01)
    return f"{format_timecode(start)}-{format_timecode(end)}"


def normalize_timestamp_label(text: str) -> str:
    """Excel/ручной ввод: unicode-тире, пробелы вокруг «-»."""
    raw = (text or "").strip().translate(_DASHES)
    if not raw:
        return ""
    return re.sub(r"\s*-\s*", "-", raw)


def _parse_one_timecode(part: str) -> float | None:
    part = normalize_timestamp_label(part)
    m = _TIMECODE_RE.match(part)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 100.0
    m = _TIMECODE_LEGACY_RE.match(part)
    if m:
        return float(int(m.group(1)) * 60 + int(m.group(2)))
    return None


def parse_timecode_range(text: str) -> tuple[float, float] | None:
    raw = normalize_timestamp_label(text)
    if "-" not in raw:
        return None
    start_s, end_s = raw.split("-", 1)
    start = _parse_one_timecode(start_s)
    end = _parse_one_timecode(end_s)
    if start is None or end is None or end <= start:
        return None
    return start, end


def count_parsed_timestamp_cells(
    timestamps: list[tuple[int, str]],
) -> tuple[int, int, list[int]]:
    """(всего непустых, распарсено, номера битых кадров)."""
    filled = [(n, lbl) for n, lbl in timestamps if (lbl or "").strip()]
    bad = [n for n, lbl in filled if parse_timecode_range(lbl) is None]
    return len(filled), len(filled) - len(bad), bad


def clips_from_timestamp_cells(
    cells: list[tuple[int, str]],
    timestamps: list[tuple[int, str]],
    voice_full_path,
    *,
    master: float,
) -> list | None:
    """FrameAudioClip из строки 15 — start/end как в Excel, без подгонки."""
    from app.services.frame_audio import FrameAudioClip

    text_by = dict(cells)
    clips: list = []
    for num, label in timestamps:
        if not (label or "").strip():
            return None
        parsed = parse_timecode_range(label)
        if parsed is None:
            return None
        start, end = parsed
        if end <= start:
            return None
        clips.append(
            FrameAudioClip(
                frame_number=num,
                path=voice_full_path,
                text=text_by.get(num, ""),
                start_ts=round(start, 3),
                end_ts=round(end, 3),
                duration=round(end - start, 3),
            )
        )
    _ = master
    return clips or None


async def load_assembly_timeline_from_r15(
    project,
    frame_numbers: list[int],
    cells: list[tuple[int, str]],
    voice_full_path,
) -> tuple[list | None, float | None]:
    """Таймлайн из R15 project.xlsx. None если строка пустая."""
    master = await probe_duration(voice_full_path)
    ts_cells, ts_row = read_plan_timestamps_cells(project, frame_numbers)
    filled_n, parsed_n, bad = count_parsed_timestamp_cells(ts_cells)
    if filled_n == 0:
        logger.warning(
            "[#{}] R{} пуста — сборка без Excel-таймкодов",
            project.id,
            ts_row,
        )
        return None, None

    if bad:
        sample = ", ".join(str(n) for n in bad[:8])
        raise RuntimeError(
            f"лист «план» R{ts_row}: не читаются метки кадров {sample}"
            f"{f' (+{len(bad) - 8})' if len(bad) > 8 else ''} — формат 0:03.28-0:05.76"
        )

    clips = clips_from_timestamp_cells(cells, ts_cells, voice_full_path, master=master)
    if clips is None or len(clips) != len(frame_numbers):
        missing = [
            n
            for n, lbl in ts_cells
            if not (lbl or "").strip() or parse_timecode_range(lbl) is None
        ]
        sample = ", ".join(str(n) for n in missing[:8])
        raise RuntimeError(
            f"R{ts_row}: метки только для {len(clips or [])}/{len(frame_numbers)} кадров"
            f"{f' (пропуски: {sample})' if sample else ''}"
        )

    logger.info(
        "[#{}] сборка по R{} xlsx ({} кадров, frame1={} … end={:.2f}s)",
        project.id,
        ts_row,
        len(clips),
        ts_cells[0][1] if ts_cells else "?",
        clips[-1].end_ts,
    )
    return clips, master


def write_asr_timestamps_to_r15(
    project,
    clips: list,
) -> int:
    """После ASR: записать реальные метки в строку 15 листа «план»."""
    ranges = [
        (c.frame_number, format_timecode_range(c.start_ts, c.end_ts))
        for c in clips
        if c.duration > 0
    ]
    if not ranges:
        return 0
    written = write_plan_timestamps(project, ranges)
    if written:
        logger.info(
            "[#{}] plan R15: записано {} таймкодов ASR → {}",
            project.id,
            written,
            project.data_dir / "project.xlsx",
        )
    else:
        logger.warning(
            "[#{}] plan R15: не удалось записать (закрой Excel?)",
            project.id,
        )
    return written
