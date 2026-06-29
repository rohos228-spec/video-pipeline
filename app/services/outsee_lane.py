"""Глобальная очередь Outsee (вариант A): одна операция за раз.

Image и video делят один lock — один Chrome, один outsee.io.
Параллельные Generate/Повторить ломают «последний результат — наш».
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from loguru import logger

from app.settings import settings

_OUTSEE_LOCK = asyncio.Lock()
_HOLDER: str | None = None
_MARKER = settings.sqlite_path.parent / ".outsee_lane.lock"


def _write_marker(text: str) -> None:
    _MARKER.parent.mkdir(parents=True, exist_ok=True)
    _MARKER.write_text(text, encoding="utf-8")


def _clear_marker() -> None:
    _MARKER.unlink(missing_ok=True)


def outsee_lane_active() -> bool:
    """Outsee держит lock (этот процесс или маркер для других скриптов)."""
    if _OUTSEE_LOCK.locked():
        return True
    return _MARKER.is_file()


def outsee_lane_busy() -> bool:
    return outsee_lane_active()


@asynccontextmanager
async def outsee_lane(
    *,
    project_id: int | None,
    op: str,
) -> AsyncIterator[None]:
    """Эксклюзивный доступ к outsee.io до конца generate/regenerate."""
    global _HOLDER
    label = f"#{project_id}" if project_id is not None else "?"
    logger.info(
        "outsee_lane: ждём lock op={} project={} (holder={})",
        op,
        label,
        _HOLDER or "—",
    )
    async with _OUTSEE_LOCK:
        _HOLDER = f"{op} {label}"
        _write_marker(_HOLDER)
        logger.info("outsee_lane: lock взят {}", _HOLDER)
        try:
            yield
        finally:
            logger.info("outsee_lane: lock снят {}", _HOLDER)
            _HOLDER = None
            _clear_marker()


# Обратная совместимость
outsee_image_lane = outsee_lane
