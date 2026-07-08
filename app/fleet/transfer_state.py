"""Fleet transfer progress + отмена по STOP (без автоперезапуска)."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from typing import Any

from loguru import logger

_lock = asyncio.Lock()
_transfers: dict[str, dict[str, Any]] = {}
_cancelled: set[int] = set()
_blocked: set[int] = set()  # до явного push — никаких active-обновлений
_active_tasks: dict[int, set[asyncio.Task[Any]]] = defaultdict(set)


class FleetTransferCancelled(Exception):
    """Пользователь нажал STOP / ✕ во время push/pull bundle."""


def _key(project_id: int, job: str = "handoff") -> str:
    return f"p{project_id}:{job}"


def is_transfer_cancelled(project_id: int) -> bool:
    return project_id in _cancelled or project_id in _blocked


def is_transfer_blocked(project_id: int) -> bool:
    return project_id in _blocked


def request_transfer_cancel(project_id: int) -> None:
    _cancelled.add(project_id)


def block_transfer_restarts(project_id: int) -> None:
    """После ✕/STOP — не возобновлять, пока пользователь не нажмёт «Отправить»."""
    _blocked.add(project_id)
    _cancelled.add(project_id)


def allow_transfer_start(project_id: int) -> None:
    """Новая ручная отправка — снять блок."""
    _blocked.discard(project_id)
    _cancelled.discard(project_id)


def clear_transfer_cancel(project_id: int) -> None:
    """Alias для тестов и старого кода."""
    allow_transfer_start(project_id)


def register_transfer_task(project_id: int, task: asyncio.Task[Any]) -> None:
    _active_tasks[project_id].add(task)

    def _done(t: asyncio.Task[Any]) -> None:
        _active_tasks[project_id].discard(t)
        if not _active_tasks[project_id]:
            _active_tasks.pop(project_id, None)

    task.add_done_callback(_done)


def is_transfer_running(project_id: int) -> bool:
    if project_id in _active_tasks and _active_tasks[project_id]:
        return True
    rec = _transfers.get(_key(project_id))
    return rec is not None and rec.get("status") == "active"


async def cancel_fleet_transfer(project_id: int) -> bool:
    """⏹ STOP / ✕: прервать и заблокировать автоперезапуск."""
    had_active = (
        is_transfer_running(project_id)
        or get_project_transfer(project_id) is not None
        or bool(_active_tasks.get(project_id))
    )
    was_engaged = (
        had_active
        or project_id in _cancelled
        or project_id in _blocked
    )
    block_transfer_restarts(project_id)
    for task in list(_active_tasks.get(project_id, ())):
        task.cancel()
    if had_active:
        logger.info("[#{}] fleet transfer CANCEL + block restart", project_id)
        await update_fleet_transfer(
            project_id,
            phase="cancelled",
            direction="",
            percent=0,
            message="⏹ Остановлено — для повтора нажми «Отправить»",
            status="error",
            force=True,
        )
    else:
        logger.debug("[#{}] fleet transfer block (no active push/pull)", project_id)
    return was_engaged


def check_transfer_cancelled(project_id: int) -> None:
    if is_transfer_cancelled(project_id):
        raise FleetTransferCancelled(f"project #{project_id} transfer cancelled")


def list_active_transfers() -> list[dict[str, Any]]:
    now = time.monotonic()
    out: list[dict[str, Any]] = []
    for rec in _transfers.values():
        if rec.get("status") != "active":
            continue
        pid = int(rec.get("project_id") or 0)
        if is_transfer_blocked(pid):
            continue
        if now - float(rec.get("updated_at_mono", 0)) > 7200:
            continue
        out.append(dict(rec))
    out.sort(key=lambda r: float(r.get("updated_at_mono", 0)), reverse=True)
    return out


def get_project_transfer(project_id: int, job: str = "handoff") -> dict[str, Any] | None:
    if is_transfer_blocked(project_id):
        return None
    rec = _transfers.get(_key(project_id, job))
    if not rec or rec.get("status") != "active":
        return None
    return dict(rec)


async def update_fleet_transfer(
    project_id: int,
    *,
    job: str = "handoff",
    phase: str,
    direction: str = "",
    percent: int = 0,
    sent_mb: float = 0,
    total_mb: float = 0,
    message: str = "",
    source_node: str = "",
    target: str = "",
    slug: str = "",
    status: str = "active",
    force: bool = False,
) -> None:
    """Обновить прогресс и отправить fleet_transfer в WebSocket."""
    if status == "active" and is_transfer_blocked(project_id) and not force:
        logger.debug("[#{}] fleet transfer blocked — ignore active {}", project_id, phase)
        return

    key = _key(project_id, job)
    pct = max(0, min(100, int(percent)))
    rec: dict[str, Any] = {
        "project_id": project_id,
        "job": job,
        "phase": phase,
        "direction": direction,
        "percent": pct,
        "sent_mb": round(sent_mb, 1),
        "total_mb": round(total_mb, 1),
        "message": message,
        "source_node": source_node,
        "target": target,
        "slug": slug,
        "status": status,
        "updated_at_mono": time.monotonic(),
    }
    async with _lock:
        _transfers[key] = rec

    from app.services.event_bus import publish_fleet_transfer_event

    await publish_fleet_transfer_event(project_id, payload=rec)
    if status != "active":
        async with _lock:
            _transfers.pop(key, None)


def emit_fleet_transfer_sync(
    project_id: int,
    *,
    job: str = "handoff",
    phase: str,
    direction: str = "",
    percent: int = 0,
    sent_mb: float = 0,
    total_mb: float = 0,
    message: str = "",
    source_node: str = "",
    target: str = "",
    slug: str = "",
    status: str = "active",
) -> None:
    """Из sync-кода / другого потока (tar, file read)."""
    if status == "active" and is_transfer_blocked(project_id):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("[#{}] fleet transfer {} (no event loop)", project_id, phase)
        return

    async def _run() -> None:
        await update_fleet_transfer(
            project_id,
            job=job,
            phase=phase,
            direction=direction,
            percent=percent,
            sent_mb=sent_mb,
            total_mb=total_mb,
            message=message,
            source_node=source_node,
            target=target,
            slug=slug,
            status=status,
        )

    try:
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(_run(), loop)
        else:
            loop.create_task(_run())
    except Exception as exc:  # noqa: BLE001
        logger.debug("[#{}] fleet transfer emit failed: {}", project_id, exc)


def parse_project_id_from_label(label: str) -> int | None:
    if not label.startswith("[#"):
        return None
    try:
        return int(label.split("]", 1)[0][2:])
    except (ValueError, IndexError):
        return None
