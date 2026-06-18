"""Hub: авто-забор проектов music_ready с agents."""

from __future__ import annotations

import asyncio

from loguru import logger
from sqlalchemy import select

from app.db import session_scope
from app.fleet import bundle as bundle_svc
from app.fleet.client import agent_get, agent_get_bytes
from app.fleet.montage_queue import enqueue_for_montage, process_montage_queue
from app.models import FleetNode, FleetNodeStatus
from app.settings import settings

_pull_task: asyncio.Task | None = None
_pulled_slugs: set[str] = set()


async def _pull_once() -> None:
    if not settings.fleet_enabled or not settings.fleet_montage_hub:
        return
    if (settings.fleet_role or "hub").lower() != "hub":
        return

    async with session_scope() as session:
        nodes = (
            await session.execute(
                select(FleetNode).where(FleetNode.is_main.is_(False))
            )
        ).scalars().all()

    for node in nodes:
        if node.status not in {FleetNodeStatus.online, FleetNodeStatus.busy}:
            continue
        token = node.token or settings.fleet_agent_token
        try:
            data = await agent_get(node.base_url, token, "/api/fleet/local/pipeline")
        except Exception as exc:  # noqa: BLE001
            logger.debug("fleet pull: {} unreachable: {}", node.name, exc)
            continue

        for proj in data.get("projects") or []:
            if not proj.get("montage_ready"):
                continue
            slug = proj.get("slug") or ""
            pull_key = f"{node.name}:{slug}"
            if pull_key in _pulled_slugs:
                continue
            pid = proj.get("id")
            if not pid:
                continue
            try:
                blob = await agent_get_bytes(
                    node.base_url,
                    token,
                    f"/api/fleet/local/projects/{pid}/export-bundle",
                    timeout_sec=600,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("fleet pull bundle {}#{} failed: {}", node.name, pid, exc)
                continue

            async with session_scope() as session:
                project = await bundle_svc.import_project_bundle(
                    session, blob, run_assemble=False
                )
                meta = dict(project.meta or {})
                meta["fleet_source_node"] = node.name
                meta["fleet_source_project_id"] = pid
                project.meta = meta
                await enqueue_for_montage(session, project, source_node=node.name)
                await process_montage_queue(session)
                await session.commit()
                logger.info(
                    "fleet pull: imported {} from {} → montage queue",
                    slug,
                    node.name,
                )
            _pulled_slugs.add(pull_key)


async def _pull_loop() -> None:
    while True:
        try:
            await _pull_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("fleet pull loop error: {}", exc)
        await asyncio.sleep(45)


def start_fleet_pull_loop() -> None:
    global _pull_task
    if not settings.fleet_auto_pull or not settings.fleet_montage_hub:
        return
    if _pull_task and not _pull_task.done():
        return
    _pull_task = asyncio.create_task(_pull_loop())
    logger.info("fleet auto-pull loop started")
