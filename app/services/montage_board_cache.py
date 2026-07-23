"""In-process кэши для панели монтажа (длительности видео, Excel)."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any, Callable

from loguru import logger

from app.services.media_probe import probe_duration
from app.services.plan_shot2 import Shot2ColumnInfo, read_shot2_columns

# Параллельный ffprobe: раньше 4 — на 300 клипах холодно ~2 мин.
_FFPROBE_SEM = asyncio.Semaphore(12)

# (path, mtime, size) -> duration seconds
_video_duration_cache: dict[tuple[str, float, int], float] = {}

# (xlsx_path, mtime) -> plan cells dict
_plan_excel_cache: dict[tuple[str, float], dict[int, dict[str, Any]]] = {}

# (xlsx_path, mtime) -> shot2 columns
_shot2_excel_cache: dict[tuple[str, float], dict[int, Shot2ColumnInfo]] = {}

# (xlsx_path, mtime, frame_sig) -> prompts
_prompts_excel_cache: dict[tuple[str, float, str], dict[int, dict[str, str]]] = {}

_disk_cache_lock = threading.Lock()
_disk_cache_loaded = False


def clear_montage_board_caches() -> None:
    """Сброс кэшей (тесты)."""
    global _disk_cache_loaded
    _video_duration_cache.clear()
    _plan_excel_cache.clear()
    _shot2_excel_cache.clear()
    _prompts_excel_cache.clear()
    _disk_cache_loaded = False


def _duration_disk_path() -> Path:
    from app.settings import settings

    root = Path(settings.data_dir)
    return root / ".cache" / "montage_video_durations.json"


def _ensure_disk_duration_cache_loaded() -> None:
    """Поднять длительности с диска после рестарта бэкенда (иначе снова 2 мин ffprobe)."""
    global _disk_cache_loaded
    if _disk_cache_loaded:
        return
    with _disk_cache_lock:
        if _disk_cache_loaded:
            return
        path = _duration_disk_path()
        if path.is_file():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        try:
                            parts = str(k).rsplit("|", 2)
                            if len(parts) != 3:
                                continue
                            pth, mtime_s, size_s = parts
                            key = (pth, float(mtime_s), int(size_s))
                            if isinstance(v, (int, float)) and v > 0:
                                _video_duration_cache[key] = float(v)
                        except (TypeError, ValueError):
                            continue
            except Exception as e:  # noqa: BLE001
                logger.debug("montage_board_cache: disk load {}: {}", path, e)
        _disk_cache_loaded = True


def _persist_disk_duration_cache() -> None:
    path = _duration_disk_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            f"{p}|{mt}|{sz}": dur
            for (p, mt, sz), dur in list(_video_duration_cache.items())
        }
        # Ограничим рост файла
        if len(payload) > 5000:
            items = list(payload.items())[-4000:]
            payload = dict(items)
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        logger.debug("montage_board_cache: disk save {}: {}", path, e)


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
    _ensure_disk_duration_cache_loaded()
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
    _ensure_disk_duration_cache_loaded()
    before = len(_video_duration_cache)
    out = list(await asyncio.gather(*(cached_probe_video_duration(p) for p in paths)))
    if len(_video_duration_cache) > before:
        await asyncio.to_thread(_persist_disk_duration_cache)
    return out


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


def get_cached_source_prompts(
    xlsx_path: Path,
    *,
    frame_sig: str,
    loader: Callable[[], dict[int, dict[str, str]]],
) -> dict[int, dict[str, str]]:
    """Кэш промтов R45/46/48/64 — иначе openpyxl на каждый GET монтажа."""
    key_base = _xlsx_mtime_key(xlsx_path)
    if key_base is None:
        return loader()
    key = (key_base[0], key_base[1], frame_sig)
    hit = _prompts_excel_cache.get(key)
    if hit is not None:
        return hit
    data = loader()
    _prompts_excel_cache[key] = data
    return data
