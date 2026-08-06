"""Глобальные потоки Studio (data/runtime_streams.json).

Per-project meta.img_streams / check_streams перекрывают defaults.
WORKER_MAX_PARALLEL — сколько проектов очереди крутятся сразу.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from loguru import logger

from app.settings import settings

WORKER_MIN = 1
WORKER_MAX = 4
OUTSEE_MIN = 0
OUTSEE_MAX = 4
CHECK_MIN = 0
CHECK_MAX = 10


def _path() -> Path:
    path = settings.data_dir / "runtime_streams.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _clamp(raw: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = default
    return max(lo, min(hi, n))


def _env_worker() -> int:
    try:
        n = int(settings.worker_max_parallel)
    except (TypeError, ValueError):
        n = 1
    return _clamp(n, WORKER_MIN, WORKER_MAX, 1)


def _env_outsee() -> int:
    try:
        n = int(getattr(settings, "img_max_streams", 1) or 1)
    except (TypeError, ValueError):
        n = 1
    return _clamp(n, OUTSEE_MIN, OUTSEE_MAX, 1)


def _env_check() -> int:
    try:
        n = int(getattr(settings, "check_max_streams", 2) or 2)
    except (TypeError, ValueError):
        n = 2
    return _clamp(n, CHECK_MIN, CHECK_MAX, 2)


def load_runtime_streams() -> dict[str, int]:
    path = _path()
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except Exception as e:  # noqa: BLE001
            logger.warning("runtime_streams: read failed: {}", e)
    return {
        "worker_max_parallel": _clamp(
            data.get("worker_max_parallel"), WORKER_MIN, WORKER_MAX, _env_worker()
        ),
        "default_outsee_streams": _clamp(
            data.get("default_outsee_streams"),
            OUTSEE_MIN,
            OUTSEE_MAX,
            _env_outsee(),
        ),
        "default_check_streams": _clamp(
            data.get("default_check_streams"),
            CHECK_MIN,
            CHECK_MAX,
            _env_check(),
        ),
    }


def save_runtime_streams(patch: dict[str, Any]) -> dict[str, int]:
    cur = load_runtime_streams()
    if "worker_max_parallel" in patch and patch["worker_max_parallel"] is not None:
        cur["worker_max_parallel"] = _clamp(
            patch["worker_max_parallel"], WORKER_MIN, WORKER_MAX, cur["worker_max_parallel"]
        )
    if "default_outsee_streams" in patch and patch["default_outsee_streams"] is not None:
        cur["default_outsee_streams"] = _clamp(
            patch["default_outsee_streams"],
            OUTSEE_MIN,
            OUTSEE_MAX,
            cur["default_outsee_streams"],
        )
    if "default_check_streams" in patch and patch["default_check_streams"] is not None:
        cur["default_check_streams"] = _clamp(
            patch["default_check_streams"],
            CHECK_MIN,
            CHECK_MAX,
            cur["default_check_streams"],
        )
    path = _path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    logger.info("runtime_streams saved: {}", cur)
    return cur


def get_worker_max_parallel() -> int:
    return load_runtime_streams()["worker_max_parallel"]


def get_default_outsee_streams() -> int:
    return load_runtime_streams()["default_outsee_streams"]


def get_default_check_streams() -> int:
    return load_runtime_streams()["default_check_streams"]
