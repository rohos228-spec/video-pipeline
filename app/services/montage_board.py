"""Данные для панели монтажа над нодой assemble (кадры × медиа × Excel)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.services.plan_shot2 import (
    find_shot1_image,
    find_shot2_image,
    plan_column_for_frame,
    shot2_video_file_pattern,
)
from app.services.xlsx_v8_import import (
    ROW_VOICEOVER_V8,
    _cell_text,
    _resolve_plan_sheet,
)

ROW_CHARACTERS_V8 = 7


def _preview_url(path: Path | None) -> str | None:
    if path is None or not path.is_file():
        return None
    return f"/api/files?path={path}"


def _find_shot1_video(videos_dir: Path, frame_number: int) -> Path | None:
    if not videos_dir.is_dir():
        return None
    candidates = [
        p
        for p in videos_dir.glob(f"clip_{frame_number:03d}_*.mp4")
        if "_s2_" not in p.name
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _find_shot2_video(videos_dir: Path, frame_number: int) -> Path | None:
    if not videos_dir.is_dir():
        return None
    candidates = list(videos_dir.glob(shot2_video_file_pattern(frame_number)))
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _read_plan_excel_cells(xlsx_path: Path) -> dict[int, dict[str, str]]:
    """frame_number → {characters, voiceover_excel}."""
    out: dict[int, dict[str, str]] = {}
    if not xlsx_path.is_file():
        return out
    try:
        wb = load_workbook(filename=str(xlsx_path), data_only=True, read_only=True)
    except Exception as e:  # noqa: BLE001
        logger.warning("montage_board: openpyxl {}: {}", xlsx_path, e)
        return out
    try:
        ws = _resolve_plan_sheet(wb)
        if ws is None:
            return out
        max_col = ws.max_column or 0
        for col in range(3, max_col + 1):
            voice = (_cell_text(ws, ROW_VOICEOVER_V8, col) or "").strip()
            chars = (_cell_text(ws, ROW_CHARACTERS_V8, col) or "").strip()
            if not voice and not chars:
                continue
            frame_num = col - 2
            if frame_num < 1:
                continue
            out[frame_num] = {
                "characters": chars,
                "voiceover_excel": voice,
            }
    finally:
        wb.close()
    return out


async def build_montage_board(
    session: AsyncSession,
    project: Project,
) -> dict[str, Any]:
    frames = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number.asc())
        )
    ).scalars().all()

    xlsx_path = project.data_dir / "project.xlsx"
    excel_by_frame = _read_plan_excel_cells(xlsx_path)
    scenes_dir = project.data_dir / "scenes"
    videos_dir = project.data_dir / "videos"

    rows: list[dict[str, Any]] = []
    for fr in frames:
        ex = excel_by_frame.get(fr.number, {})
        img1 = find_shot1_image(scenes_dir, fr.number)
        img2 = find_shot2_image(scenes_dir, fr.number)
        vid1 = _find_shot1_video(videos_dir, fr.number)
        vid2 = _find_shot2_video(videos_dir, fr.number)
        rows.append(
            {
                "frame_id": fr.id,
                "number": fr.number,
                "voiceover_text": fr.voiceover_text or "",
                "voiceover_excel": ex.get("voiceover_excel") or "",
                "characters": ex.get("characters") or "",
                "start_ts": fr.start_ts,
                "end_ts": fr.end_ts,
                "duration_seconds": fr.duration_seconds,
                "image_shot1_url": _preview_url(img1),
                "image_shot2_url": _preview_url(img2),
                "video_shot1_url": _preview_url(vid1),
                "video_shot2_url": _preview_url(vid2),
                "plan_column": plan_column_for_frame(fr.number),
            }
        )

    return {"frames": rows, "frame_count": len(rows)}
