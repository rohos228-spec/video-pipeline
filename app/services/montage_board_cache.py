"""In-process кэши для панели монтажа (длительности видео, Excel)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.media_probe import probe_duration
from app.services.plan_shot2 import Shot2ColumnInfo, read_shot2_columns

_FFPROBE_SEM = asyncio.Semaphore(4)

# (path, mtime, size) -> duration seconds
_video_duration_cache: dict[tuple[str, float, int], float] = {}

# (xlsx_path, mtime) -> plan cells dict
_plan_excel_cache: dict[tuple[str, float], dict[int, dict[str, Any]]] = {}

# (xlsx_path, mtime) -> shot2 columns
_shot2_excel_cache: dict[tuple[str, float], dict[int, Shot2ColumnInfo]] = {}


def clear_montage_board_caches() -> None:
    """Сброс кэшей (тесты)."""
    _video_duration_cache.clear()
    _plan_excel_cache.clear()
    _shot2_excel_cache.clear()


def _file_stat_key(path: Path) -> tuple[str, float, int] | None:
    if not path.is_file():
        return None
    try:
        st = path.stat()
        return (str(path.resolve()), st.st_mtime, st.st_size)
    except OSError:
        return None


def _xlsx_mtime_key(path: Path) -> tuple[str, float] | None:
    if not path.is_file():
        return None
    try:
        return (str(path.resolve()), path.stat().st_mtime)
    except OSError:
        return None


async def cached_probe_video_duration(path: Path | None) -> float | None:
    if path is None or not path.is_file():
        return None
    key = _file_stat_key(path)
    if key is None:
        return None
    cached = _video_duration_cache.get(key)
    if cached is not None:
        return cached
    async with _FFPROBE_SEM:
        # повторная проверка после ожидания семафора
        key2 = _file_stat_key(path)
        if key2 is None:
            return None
        hit = _video_duration_cache.get(key2)
        if hit is not None:
            return hit
        try:
            dur = round(await probe_duration(path), 3)
            _video_duration_cache[key2] = dur
            return dur
        except Exception as e:  # noqa: BLE001
            logger.debug("montage_board_cache: probe {}: {}", path, e)
            return None


async def probe_video_durations_parallel(paths: list[Path | None]) -> list[float | None]:
    return list(await asyncio.gather(*(cached_probe_video_duration(p) for p in paths)))


def get_cached_plan_excel_cells(
    xlsx_path: Path,
    *,
    loader: Any,
) -> dict[int, dict[str, Any]]:
    """loader: callable(xlsx_path) -> dict."""
    key = _xlsx_mtime_key(xlsx_path)
    if key is None:
        return {}
    hit = _plan_excel_cache.get(key)
    if hit is not None:
        return hit
    data = loader(xlsx_path)
    _plan_excel_cache[key] = data
    return data


def get_cached_shot2_columns(xlsx_path: Path) -> dict[int, Shot2ColumnInfo]:
    key = _xlsx_mtime_key(xlsx_path)
    if key is None:
        return {}
    hit = _shot2_excel_cache.get(key)
    if hit is not None:
        return hit
    data = read_shot2_columns(xlsx_path)
    _shot2_excel_cache[key] = data
    return data
