"""Agent executes hub-requested fleet actions (outbound-only)."""

from __future__ import annotations

import platform
import urllib.parse

from loguru import logger
from sqlalchemy import select

from app.db import session_scope
from app.fleet import bundle as bundle_svc
from app.fleet.client import FleetAgentError, agent_upload_file
from app.models import Project
from app.settings import settings


async def _export_for_action(project_id: int, slug: str | None) -> tuple[bytes, str]:
    async with session_scope() as session:
        if project_id:
            try:
                return await bundle_svc.export_project_bundle(session, project_id)
            except ValueError:
                if not slug:
                    raise
        if slug:
            row = (
                await session.execute(select(Project).where(Project.slug == slug))
            ).scalar_one_or_none()
            if row is None:
                raise ValueError(f"project slug {slug!r} not found")
            return await bundle_svc.export_project_bundle(session, row.id)
        raise ValueError(f"project #{project_id} not found")


async def execute_pending_fleet_actions(actions: list[dict]) -> list[dict]:
    results: list[dict] = []
    if not actions:
        return results
    role = (settings.fleet_role or "hub").strip().lower()
    if role != "agent":
        return results
    hub = (settings.fleet_hub_url or "").strip().rstrip("/")
    if not hub:
        return results
    token = settings.fleet_agent_token or ""
    source_node = (settings.fleet_node_name or "").strip() or platform.node()

    for action in actions:
        if (action.get("type") or "") != "pull_to_hub":
            continue
        project_id = action.get("project_id")
        slug = action.get("slug")
        if not project_id and not slug:
            continue
        run_assemble = bool(action.get("run_assemble", True))
        try:
            blob, filename = await _export_for_action(int(project_id or 0), slug)
            qs = urllib.parse.urlencode(
                {
                    "run_assemble": "true" if run_assemble else "false",
                    "source_node": source_node,
                    "source_project_id": str(project_id or ""),
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
            logger.info("fleet agent: pull_to_hub #{} ({}) → hub ok", project_id, slug or "")
            results.append(
                {"project_id": project_id, "slug": slug, "ok": True}
            )
        except FleetAgentError as exc:
            logger.warning("fleet agent pull_to_hub #{} failed: {}", project_id, exc)
            results.append(
                {"project_id": project_id, "slug": slug, "ok": False, "error": str(exc)[:300]}
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("fleet agent pull_to_hub #{} error: {}", project_id, exc)
            results.append(
                {"project_id": project_id, "slug": slug, "ok": False, "error": str(exc)[:300]}
            )
    return results
