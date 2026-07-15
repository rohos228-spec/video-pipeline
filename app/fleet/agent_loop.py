"""Agent heartbeat loop (registers this station on hub)."""

from __future__ import annotations

import asyncio
import platform

from loguru import logger

from app.fleet.client import FleetAgentError, agent_post
from app.fleet.pipeline_list import build_pipeline_payload
from app.fleet.self_node import is_localhost_fleet_url
from app.settings import settings

_agent_task: asyncio.Task | None = None


async def _heartbeat_once() -> None:
    if not settings.fleet_enabled:
        return
    role = (settings.fleet_role or "hub").strip().lower()
    if role != "agent":
        return
    hub = (settings.fleet_hub_url or "").strip().rstrip("/")
    if not hub:
        return
    name = (settings.fleet_node_name or "").strip() or platform.node()
    if name in ("main-pc", "hub"):
        name = platform.node() or "child-pc"
    try:
        pipe = await build_pipeline_payload()
        projects = pipe.get("projects") or []
    except Exception as exc:  # noqa: BLE001
        logger.warning("fleet agent: pipeline snapshot failed: {}", exc)
        projects = []
    body = {
        "name": name,
        "base_url": settings.fleet_agent_base_url,
        "hostname": platform.node(),
        "role": "agent",
        "is_main": False,
        "projects": projects,
    }
    pub = (settings.fleet_public_url or "").strip()
    if not pub or is_localhost_fleet_url(pub):
        logger.debug(
            "fleet agent: FLEET_PUBLIC_URL not set ({}), hub will use heartbeat cache",
            body["base_url"],
        )
    token = settings.fleet_agent_token or ""
    try:
        await agent_post(hub, token, "/api/fleet/register", json_body=body, timeout_sec=30)
        logger.info("fleet agent heartbeat ok: {} projects → hub", len(projects))
    except FleetAgentError as exc:
        logger.warning("fleet agent heartbeat failed: {}", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("fleet agent heartbeat error: {}", exc)


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

    async def _kick() -> None:
        await _heartbeat_once()

    asyncio.create_task(_kick())
    _agent_task = asyncio.create_task(_agent_loop())
    logger.info("fleet agent heartbeat loop started")
