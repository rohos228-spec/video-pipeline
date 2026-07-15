"""Agent heartbeat loop (registers this station on hub)."""

from __future__ import annotations

import asyncio
import platform

from loguru import logger

from app.fleet.client import FleetAgentError, agent_post
from app.fleet.agent_actions import execute_pending_fleet_actions
from app.fleet.pipeline_list import build_pipeline_payload
from app.fleet.self_node import is_localhost_fleet_url
from app.settings import settings

_agent_task: asyncio.Task | None = None
_heartbeat_interval_sec: float = 5.0
_last_pull_results: list[dict] = []


async def _heartbeat_once() -> float:
    global _last_pull_results
    if not settings.fleet_enabled:
        return _heartbeat_interval_sec
    role = (settings.fleet_role or "hub").strip().lower()
    if role != "agent":
        return _heartbeat_interval_sec
    hub = (settings.fleet_hub_url or "").strip().rstrip("/")
    if not hub:
        return _heartbeat_interval_sec
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
    if _last_pull_results:
        body["pull_results"] = _last_pull_results
        _last_pull_results = []
    pub = (settings.fleet_public_url or "").strip()
    if not pub or is_localhost_fleet_url(pub):
        logger.debug(
            "fleet agent: FLEET_PUBLIC_URL not set ({}), hub will use heartbeat cache",
            body["base_url"],
        )
    token = settings.fleet_agent_token or ""
    try:
        result = await agent_post(hub, token, "/api/fleet/register", json_body=body, timeout_sec=60)
        pending = result.get("pending_actions") if isinstance(result, dict) else []
        if pending:
            logger.info("fleet agent: hub queued {} action(s)", len(pending))
        pull_results = await execute_pending_fleet_actions(pending or [])
        if pull_results:
            _last_pull_results = pull_results
        logger.info("fleet agent heartbeat ok: {} projects → hub", len(projects))
        next_sec = float(result.get("next_heartbeat_sec") or 5) if isinstance(result, dict) else 5.0
        return max(3.0, min(next_sec, 30.0))
    except FleetAgentError as exc:
        logger.warning("fleet agent heartbeat failed: {}", exc)
    except Exception as exc:  # noqa: BLE001
        logger.warning("fleet agent heartbeat error: {}", exc)
    return _heartbeat_interval_sec


async def _agent_loop() -> None:
    global _heartbeat_interval_sec
    while True:
        try:
            _heartbeat_interval_sec = await _heartbeat_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("fleet agent loop: {}", exc)
        await asyncio.sleep(_heartbeat_interval_sec)


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
