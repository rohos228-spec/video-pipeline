"""Agent heartbeat loop (registers this station on hub)."""

from __future__ import annotations

import asyncio
import platform

from loguru import logger

from app.fleet.client import FleetAgentError, agent_post
from app.settings import settings

_agent_task: asyncio.Task | None = None


async def _heartbeat_once() -> None:
    if not settings.fleet_enabled:
        return
    role = (settings.fleet_role or "hub").strip().lower()
    if role != "agent":
        return
    hub = (settings.fleet_heartbeat_hub_url or "").strip().rstrip("/")
    if not hub:
        return
    body = {
        "name": settings.fleet_node_name or platform.node(),
        "base_url": settings.fleet_agent_base_url,
        "hostname": platform.node(),
        "role": "agent",
        "is_main": settings.fleet_is_main,
    }
    token = settings.fleet_agent_token or ""
    try:
        await agent_post(hub, token, "/api/fleet/register", json_body=body, timeout_sec=20)
    except FleetAgentError as exc:
        logger.debug("fleet agent heartbeat failed: {}", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("fleet agent heartbeat error (hub={}): {}", hub, exc)


async def _agent_loop() -> None:
    while True:
        try:
            await _heartbeat_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("fleet agent loop: {}", exc)
        await asyncio.sleep(30)


def start_fleet_agent() -> None:
    global _agent_task
    if not settings.fleet_enabled:
        return
    if (settings.fleet_role or "hub").strip().lower() != "agent":
        return
    if _agent_task and not _agent_task.done():
        return
    _agent_task = asyncio.create_task(_agent_loop())
    logger.info("fleet agent heartbeat loop started")
