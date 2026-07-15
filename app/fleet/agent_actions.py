"""Agent executes hub-requested fleet actions (outbound-only)."""

from __future__ import annotations

import platform
import urllib.parse

from loguru import logger

from app.db import session_scope
from app.fleet import bundle as bundle_svc
from app.fleet.client import FleetAgentError, agent_upload_file
from app.settings import settings


async def execute_pending_fleet_actions(actions: list[dict]) -> None:
    if not actions:
        return
    role = (settings.fleet_role or "hub").strip().lower()
    if role != "agent":
        return
    hub = (settings.fleet_hub_url or "").strip().rstrip("/")
    if not hub:
        return
    token = settings.fleet_agent_token or ""
    source_node = (settings.fleet_node_name or "").strip() or platform.node()

    for action in actions:
        if (action.get("type") or "") != "pull_to_hub":
            continue
        project_id = action.get("project_id")
        if not project_id:
            continue
        run_assemble = bool(action.get("run_assemble", True))
        try:
            async with session_scope() as session:
                blob, filename = await bundle_svc.export_project_bundle(session, int(project_id))
            qs = urllib.parse.urlencode(
                {
                    "run_assemble": "true" if run_assemble else "false",
                    "source_node": source_node,
                    "source_project_id": str(project_id),
                }
            )
            await agent_upload_file(
                hub,
                token,
                f"/api/fleet/import-bundle?{qs}",
                file_bytes=blob,
                filename=filename,
                timeout_sec=600,
            )
            logger.info("fleet agent: pull_to_hub project #{} → hub ok", project_id)
        except FleetAgentError as exc:
            logger.warning("fleet agent pull_to_hub #{} failed: {}", project_id, exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("fleet agent pull_to_hub #{} error: {}", project_id, exc)
