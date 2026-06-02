"""Глобальная очередь Outsee (вариант A): одна операция за раз.

Image и video делят один lock — один Chrome, один outsee.io.
Параллельные Generate/Повторить ломают «последний результат — наш».
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from loguru import logger

_OUTSEE_LOCK = asyncio.Lock()
_HOLDER: str | None = None


def outsee_lane_busy() -> bool:
    return _OUTSEE_LOCK.locked()


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
        logger.info("outsee_lane: lock взят {}", _HOLDER)
        try:
            yield
        finally:
            logger.info("outsee_lane: lock снят {}", _HOLDER)
            _HOLDER = None


# Обратная совместимость
outsee_image_lane = outsee_lane
