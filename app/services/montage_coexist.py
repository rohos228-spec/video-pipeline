"""Параллельный монтаж: ffmpeg не трогает Outsee/Chrome.

Только per-project lock — чтобы worker и GO-MONTAGE не собрали один проект дважды.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from typing import Iterator

from loguru import logger

from app.settings import settings


def _montage_marker(project_id: int):
    return settings.sqlite_path.parent / f".montage_lane_{project_id}.lock"


def montage_lane_holder(project_id: int) -> str | None:
    marker = _montage_marker(project_id)
    if not marker.is_file():
        return None
    return marker.read_text(encoding="utf-8").strip() or None


def montage_lane_owned_by(project_id: int) -> bool:
    return _montage_marker(project_id).is_file()


@contextmanager
def montage_lane_claim(project_id: int) -> Iterator[None]:
    """Эксклюзивный монтаж одного проекта (direct script vs backend worker)."""
    marker = _montage_marker(project_id)
    if marker.is_file():
        holder = marker.read_text(encoding="utf-8").strip()
        raise RuntimeError(
            f"монтаж #{project_id} уже идёт"
            + (f" ({holder})" if holder else "")
        )
    marker.parent.mkdir(parents=True, exist_ok=True)
    label = f"assemble #{project_id}"
    marker.write_text(label, encoding="utf-8")
    logger.info("montage_lane: lock {}", label)
    try:
        yield
    finally:
        marker.unlink(missing_ok=True)
        logger.info("montage_lane: lock снят {}", label)


async def wait_for_montage_slot(
    project_id: int,
    *,
    timeout_sec: float = 600,
    poll_sec: float = 3,
) -> None:
    """Ждать только если этот же проект уже монтируется (не Outsee, не другие проекты)."""
    deadline = time.monotonic() + timeout_sec
    while montage_lane_owned_by(project_id):
        if time.monotonic() >= deadline:
            holder = montage_lane_holder(project_id) or "?"
            raise TimeoutError(
                f"монтаж #{project_id} всё ещё занят ({holder}), timeout {int(timeout_sec)}s"
            )
        print(f"Монтаж #{project_id} уже идёт — ждём завершения…")
        await asyncio.sleep(poll_sec)
