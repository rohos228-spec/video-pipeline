"""Чтение меток R15 из Excel — только строка 15, лист «план»."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from app.models import Project
from app.services.plan_timestamps import count_parsed_timestamp_cells, parse_timecode_range
from app.storage.plan_sheet_v8 import read_plan_timestamps_cells, scan_r15_frame_numbers


@dataclass(frozen=True)
class R15Marker:
    frame_number: int
    label: str
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def excel_frame_numbers(project: Project) -> list[int]:
    """Кадры по непустым ячейкам R15."""
    return scan_r15_frame_numbers(project)


def resolve_montage_frame_numbers(
    project: Project,
    db_frame_numbers: list[int],
) -> list[int]:
    """Монтаж по R15: если в Excel больше меток, чем кадров в БД — берём Excel."""
    r15_nums = scan_r15_frame_numbers(project)
    if not r15_nums:
        return db_frame_numbers
    if len(r15_nums) != len(db_frame_numbers):
        logger.warning(
            "[#{}] R15 scan: {} меток, БД {} кадров — монтаж по R15",
            project.id,
            len(r15_nums),
            len(db_frame_numbers),
        )
    return r15_nums


def r15_cells_monotonic(
    ts_cells: list[tuple[int, str]],
    *,
    tolerance: float = 0.02,
) -> bool:
    """True если метки R15 идут по шкале без overlap назад."""
    prev_end = -0.01
    for _num, label in ts_cells:
        parsed = parse_timecode_range(label)
        if parsed is None:
            return False
        start, end = parsed
        if end <= start + 0.01:
            return False
        if start < prev_end - tolerance:
            return False
        prev_end = end
    return True


def load_r15_markers(project: Project, frame_numbers: list[int]) -> tuple[list[R15Marker], int]:
    """Каждый запуск: свежее чтение project.xlsx с диска."""
    xlsx = project.data_dir / "project.xlsx"
    if not xlsx.is_file():
        raise RuntimeError(f"нет {xlsx}")

    ts_cells, ts_row = read_plan_timestamps_cells(project, frame_numbers)
    st = xlsx.stat()
    logger.info(
        "[#{}] R{} read {} mtime={} size={}",
        project.id,
        ts_row,
        xlsx,
        datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
        st.st_size,
    )

    _filled, parsed_n, bad = count_parsed_timestamp_cells(ts_cells)
    if parsed_n != len(frame_numbers):
        sample = ", ".join(str(n) for n in bad[:8]) if bad else "—"
        raise RuntimeError(
            f"R{ts_row}: прочитано {parsed_n}/{len(frame_numbers)} меток. "
            f"Сохрани Excel, закрой Office. Битые: {sample}"
        )

    markers: list[R15Marker] = []
    prev_end = -0.01
    for num, label in ts_cells:
        parsed = parse_timecode_range(label)
        if parsed is None:
            raise RuntimeError(f"кадр {num}: битая метка {label!r}")
        start, end = parsed
        if end <= start + 0.01:
            raise RuntimeError(f"кадр {num}: end<=start ({label!r})")
        if start < prev_end - 0.02:
            raise RuntimeError(
                f"кадр {num}: start {start:.3f}s < prev {prev_end:.3f}s — метки не по порядку"
            )
        markers.append(
            R15Marker(frame_number=num, label=label.strip(), start_s=start, end_s=end)
        )
        prev_end = end

    return markers, ts_row


def write_r15_proof(markers: list[R15Marker], path: Path, *, ts_row: int, voice_s: float) -> None:
    lines = [
        f"source=excel_r15_row_{ts_row}",
        f"markers={len(markers)}",
        f"voice_duration={voice_s:.3f}",
        f"last_marker_end={markers[-1].end_s:.3f}" if markers else "last_marker_end=0",
        f"voice_gap={voice_s - markers[-1].end_s:.3f}" if markers else "",
        "",
        "frame\texcel\tstart_s\tend_s\tduration_s",
    ]
    for m in markers:
        lines.append(
            f"{m.frame_number}\t{m.label}\t{m.start_s:.3f}\t{m.end_s:.3f}\t{m.duration_s:.3f}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
