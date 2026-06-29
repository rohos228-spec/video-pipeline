"""Фоновый агент: heartbeat на hub (режим agent или hub+worker)."""

from __future__ import annotations

import asyncio
import platform
import socket

import aiohttp
from loguru import logger

from app.fleet.self_node import ensure_self_fleet_node, self_node_name
from app.settings import settings

_agent_task: asyncio.Task | None = None


def _should_run_heartbeat() -> bool:
    if not settings.fleet_enabled:
        return False
    role = (settings.fleet_role or "hub").strip().lower()
    if role == "agent":
        return True
    if role == "hub" and settings.fleet_hub_is_worker:
        return True
    return False


async def _heartbeat_loop() -> None:
    hub = settings.fleet_heartbeat_hub_url
    token = settings.fleet_agent_token
    name = self_node_name()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    is_main = bool(settings.fleet_montage_hub or settings.fleet_is_main)
    role = settings.fleet_role or "hub"
    if role.lower() == "hub" and settings.fleet_hub_is_worker:
        role = "hub+worker"
    payload = {
        "name": name,
        "base_url": settings.fleet_agent_base_url,
        "hostname": platform.node(),
        "role": role,
        "is_main": is_main,
    }
    timeout = aiohttp.ClientTimeout(total=30)
    while True:
        try:
            await ensure_self_fleet_node()
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    f"{hub}/api/fleet/register",
                    json=payload,
                    headers=headers,
                ) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        logger.warning("fleet heartbeat HTTP {}: {}", resp.status, text[:200])
                    else:
                        logger.debug("fleet heartbeat ok ({})", name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("fleet heartbeat error (hub={}): {!r}", hub, exc)
        await asyncio.sleep(30)


def start_fleet_agent() -> None:
    global _agent_task
    if not _should_run_heartbeat():
        return
    if _agent_task and not _agent_task.done():
        return
    _agent_task = asyncio.create_task(_heartbeat_loop())
    logger.info(
        "fleet agent started: name={} hub={} role={}",
        self_node_name(),
        settings.fleet_heartbeat_hub_url,
        settings.fleet_role,
    )


def stop_fleet_agent() -> None:
    global _agent_task
    if _agent_task and not _agent_task.done():
        _agent_task.cancel()
    _agent_task = None
