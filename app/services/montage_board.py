"""Данные для панели монтажа над нодой assemble (кадры × медиа × Excel)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.orchestrator.steps.generate_images import (
    _XLSX_ROWS_PERSONS,
    _artifact_ref_path,
    _find_ref_file_any,
    _hero_legacy_ref,
    _parse_ref_ids,
    _resolve_plan_sheet,
)
from app.services.excel_characters import parse_persons_sheet
from app.services.montage_board_cache import (
    cached_probe_video_duration,
    get_cached_plan_excel_cells,
    get_cached_shot2_columns,
    probe_video_durations_parallel,
)
from app.services.montage_board_meta import montage_meta, public_board_meta
from app.services.montage_board_regen import (
    image_prompt_from_excel,
    video_prompt_from_excel,
)
from app.services.plan_shot2 import (
    find_shot1_image,
    find_shot2_image,
    plan_column_for_frame,
    shot2_video_file_pattern,
)
from app.services.shot2_timeline import split_voiceover_duration
from app.services.xlsx_v8_import import (
    ROW_VOICEOVER_V8,
    _cell_text,
)


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


def _character_name_map(xlsx_path: Path) -> dict[str, str]:
    try:
        chars = parse_persons_sheet(xlsx_path)
    except Exception:  # noqa: BLE001
        return {}
    return {c.id.lower(): (c.name or c.id) for c in chars if c.id}


def _merged_plan_ids(ws, col: int, rows: tuple[int, ...]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for ref_id in _parse_ref_ids(ws.cell(row=row, column=col).value):
            if ref_id not in seen:
                seen.add(ref_id)
                merged.append(ref_id)
    return merged


def _resolve_character_file(
    chars_dir: Path,
    project_data_dir: Path,
    ref_id: str,
) -> Path | None:
    """Файл персонажа: c01.png → legacy hero_N_v1_*.png."""
    return _find_ref_file_any(chars_dir, ref_id) or _hero_legacy_ref(
        project_data_dir, ref_id
    )


def _refresh_character_image_urls(
    excel_by_frame: dict[int, dict[str, Any]],
    *,
    chars_dir: Path,
    project_data_dir: Path,
) -> None:
    """Обновить image_url по диску.

    Кэш Excel ключуется только mtime xlsx: если монтаж открыли до генерации
    hero, в кэше остаются image_url=null. PNG появляются без смены xlsx —
    поэтому пути резолвим на каждый запрос.
    """
    for ex in excel_by_frame.values():
        refs = ex.get("character_refs")
        if not refs:
            continue
        for ref in refs:
            rid = str(ref.get("id") or "").strip()
            if not rid:
                ref["image_url"] = None
                continue
            ref["image_url"] = _preview_url(
                _resolve_character_file(chars_dir, project_data_dir, rid)
            )


async def _fill_missing_character_images_from_artifacts(
    session: AsyncSession,
    project_id: int,
    excel_by_frame: dict[int, dict[str, Any]],
) -> None:
    """Fallback: hero_reference из БД, если файла нет в characters/."""
    for ex in excel_by_frame.values():
        refs = ex.get("character_refs")
        if not refs:
            continue
        for ref in refs:
            if ref.get("image_url"):
                continue
            rid = str(ref.get("id") or "").strip()
            if not rid:
                continue
            path = await _artifact_ref_path(
                session, project_id, rid, kind="character"
            )
            ref["image_url"] = _preview_url(path)


def _read_plan_excel_cells_uncached(
    xlsx_path: Path,
    *,
    chars_dir: Path,
    project_data_dir: Path,
) -> dict[int, dict[str, Any]]:
    """frame_number → {voiceover_excel, characters, character_refs}."""
    out: dict[int, dict[str, Any]] = {}
    if not xlsx_path.is_file():
        return out
    names = _character_name_map(xlsx_path)
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
            person_ids = _merged_plan_ids(ws, col, _XLSX_ROWS_PERSONS)
            if not voice and not person_ids:
                continue
            frame_num = col - 2
            if frame_num < 1:
                continue
            character_refs: list[dict[str, str | None]] = []
            for ref_id in person_ids:
                image_path = _resolve_character_file(
                    chars_dir, project_data_dir, ref_id
                )
                character_refs.append(
                    {
                        "id": ref_id,
                        "name": names.get(ref_id.lower(), ref_id),
                        "image_url": _preview_url(image_path),
                    }
                )
            out[frame_num] = {
                "characters": ", ".join(person_ids),
                "voiceover_excel": voice,
                "character_refs": character_refs,
            }
    finally:
        wb.close()
    return out


def _read_plan_excel_cells(
    xlsx_path: Path,
    *,
    chars_dir: Path,
    project_data_dir: Path,
) -> dict[int, dict[str, Any]]:
    data = get_cached_plan_excel_cells(
        xlsx_path,
        loader=lambda p: _read_plan_excel_cells_uncached(
            p, chars_dir=chars_dir, project_data_dir=project_data_dir
        ),
    )
    # Даже на cache-hit — пересчитать фото с диска (xlsx mtime мог не меняться).
    _refresh_character_image_urls(
        data, chars_dir=chars_dir, project_data_dir=project_data_dir
    )
    return data


def _scene_use_durations(
    scene_seconds: float | None,
    *,
    has_shot2: bool,
) -> tuple[float | None, float | None]:
    if scene_seconds is None or scene_seconds <= 0:
        return None, None
    if has_shot2:
        d1, d2 = split_voiceover_duration(scene_seconds)
        return d1, d2
    return round(float(scene_seconds), 3), None


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
    chars_dir = project.data_dir / "characters"
    excel_by_frame = _read_plan_excel_cells(
        xlsx_path,
        chars_dir=chars_dir,
        project_data_dir=project.data_dir,
    )
    await _fill_missing_character_images_from_artifacts(
        session, project.id, excel_by_frame
    )
    shot2_by = get_cached_shot2_columns(xlsx_path) if xlsx_path.is_file() else {}
    scenes_dir = project.data_dir / "scenes"
    videos_dir = project.data_dir / "videos"

    # Собираем пути видео и зондируем параллельно (кэш по mtime/size).
    frame_videos: list[tuple[Frame, Path | None, Path | None, dict, bool, bool]] = []
    all_vid_paths: list[Path | None] = []
    for fr in frames:
        ex = excel_by_frame.get(fr.number, {})
        vid1 = _find_shot1_video(videos_dir, fr.number)
        vid2 = _find_shot2_video(videos_dir, fr.number)
        shot2_info = shot2_by.get(fr.number)
        has_shot2 = bool(shot2_info is not None and shot2_info.has_shot2)
        has_shot2_video = has_shot2 and vid2 is not None and vid2.is_file()
        frame_videos.append((fr, vid1, vid2, ex, has_shot2, has_shot2_video))
        all_vid_paths.extend([vid1, vid2])

    durations = await probe_video_durations_parallel(all_vid_paths)
    dur_iter = iter(durations)

    rows: list[dict[str, Any]] = []
    for fr, vid1, vid2, ex, has_shot2, has_shot2_video in frame_videos:
        img1 = find_shot1_image(scenes_dir, fr.number)
        img2 = find_shot2_image(scenes_dir, fr.number)
        scene_seconds = (
            float(fr.duration_seconds)
            if fr.duration_seconds is not None and fr.duration_seconds > 0
            else None
        )
        shot1_use, shot2_use = _scene_use_durations(scene_seconds, has_shot2=has_shot2_video)
        vid1_dur = next(dur_iter)
        vid2_dur = next(dur_iter)
        vo_start = float(fr.start_ts) if fr.start_ts is not None else None
        vo_end = float(fr.end_ts) if fr.end_ts is not None else None
        shot1_timeline_start = vo_start
        shot1_timeline_end = (
            round(vo_start + shot1_use, 3)
            if vo_start is not None and shot1_use is not None
            else None
        )
        shot2_timeline_start = shot1_timeline_end
        shot2_timeline_end = vo_end if has_shot2 else None

        rows.append(
            {
                "frame_id": fr.id,
                "number": fr.number,
                "voiceover_text": fr.voiceover_text or "",
                "voiceover_excel": ex.get("voiceover_excel") or "",
                "characters": ex.get("characters") or "",
                "character_refs": ex.get("character_refs") or [],
                "start_ts": fr.start_ts,
                "end_ts": fr.end_ts,
                "duration_seconds": fr.duration_seconds,
                "has_shot2": has_shot2,
                "shot1_use_seconds": shot1_use,
                "shot2_use_seconds": shot2_use,
                "shot1_timeline_start": shot1_timeline_start,
                "shot1_timeline_end": shot1_timeline_end,
                "shot2_timeline_start": shot2_timeline_start,
                "shot2_timeline_end": shot2_timeline_end,
                "video_shot1_duration": vid1_dur,
                "video_shot2_duration": vid2_dur,
                "image_shot1_url": _preview_url(img1),
                "image_shot2_url": _preview_url(img2),
                "video_shot1_url": _preview_url(vid1),
                "video_shot2_url": _preview_url(vid2),
                # Промты исходников — сразу в модалке «Редактировать промт».
                "image_prompt_shot1": image_prompt_from_excel(project, fr, 1) or "",
                "image_prompt_shot2": image_prompt_from_excel(project, fr, 2) or "",
                "animation_prompt_shot1": video_prompt_from_excel(project, fr, 1) or "",
                "animation_prompt_shot2": video_prompt_from_excel(project, fr, 2) or "",
                "plan_column": plan_column_for_frame(fr.number),
            }
        )

    board_meta = public_board_meta(montage_meta(project))
    return {"frames": rows, "frame_count": len(rows), "meta": board_meta}
