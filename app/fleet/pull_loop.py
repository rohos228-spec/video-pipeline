"""Hub: забор проектов с agents — только явные handoff, не все montage_ready."""

from __future__ import annotations

import asyncio

from loguru import logger
from sqlalchemy import select

from app.db import session_scope
from app.fleet import bundle as bundle_svc
from app.fleet.client import FleetAgentError, agent_get, agent_download_to_file, agent_post
from app.fleet.montage_queue import enqueue_for_montage, process_montage_queue
from app.models import FleetNode, FleetNodeStatus
from app.settings import settings

_pull_task: asyncio.Task | None = None
_pulled_versions: dict[str, str] = {}


def _agent_project_eligible_for_pull(proj: dict) -> bool:
    """Только явно отложенные на hub — не каждый montage_ready подряд."""
    if proj.get("fleet_handoff_complete"):
        return False
    if not proj.get("montage_handoff_pending"):
        return False
    return bool(proj.get("montage_ready"))


async def _import_bundle_from_node(
    node: FleetNode,
    *,
    project_id: int,
    slug: str,
    ready_at: str,
    skip_if_pulled: bool = True,
) -> bool:
    """Скачать bundle с agent и поставить в очередь монтажа на hub."""
    token = node.token or settings.fleet_agent_token
    pull_key = f"{node.name}:{slug or project_id}"
    if skip_if_pulled and ready_at and _pulled_versions.get(pull_key) == ready_at:
        logger.debug(
            "fleet pull: skip {}#{} — already pulled at {}",
            node.name,
            project_id,
            ready_at,
        )
        return False

    try:
        import os
        import tempfile
        from pathlib import Path

        fd, tmp_name = tempfile.mkstemp(suffix=".tar.gz", prefix="fleet-pull-")
        os.close(fd)
        bundle_path = Path(tmp_name)
        try:
            size = await agent_download_to_file(
                node.base_url,
                token,
                f"/api/fleet/local/projects/{project_id}/export-bundle",
                bundle_path,
                timeout_sec=3600,
                progress_label=f"[#{project_id}] fleet pull {node.name}",
                project_id=project_id,
            )
        except Exception:
            bundle_path.unlink(missing_ok=True)
            raise
        if size <= 0:
            bundle_path.unlink(missing_ok=True)
            return False
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "fleet pull bundle {}#{} failed (base_url={}): {}",
            node.name,
            project_id,
            node.base_url,
            exc,
        )
        return False

    version_token = ready_at
    try:
        async with session_scope() as session:
            project = await bundle_svc.import_project_bundle_file(
                session, bundle_path, run_assemble=False
            )
            meta = dict(project.meta or {})
            meta["fleet_source_node"] = node.name
            meta["fleet_source_project_id"] = project_id
            project.meta = meta
            await enqueue_for_montage(session, project, source_node=node.name)
            await process_montage_queue(session)
            await session.commit()
            version_token = ready_at or str(meta.get("montage_ready_at") or "")
            logger.info(
                "fleet pull: imported {} from {} → montage queue",
                slug or project.slug,
                node.name,
            )
    finally:
        bundle_path.unlink(missing_ok=True)

    if version_token:
        _pulled_versions[pull_key] = version_token

    try:
        await agent_post(
            node.base_url,
            token,
            f"/api/fleet/local/projects/{project_id}/handoff-complete",
            json_body={"via": "pull"},
            timeout_sec=30,
        )
    except FleetAgentError as exc:
        logger.warning(
            "fleet pull: handoff-complete notify {}#{} failed: {}",
            node.name,
            project_id,
            exc.detail[:120],
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("fleet pull: handoff-complete notify error: {}", exc)

    return True


async def pull_montage_from_agent(
    node_name: str,
    project_id: int,
    *,
    slug: str = "",
    ready_at: str = "",
    force: bool = False,
) -> dict[str, object]:
    """Точечный забор одного проекта (вызов с hub по уведомлению от agent)."""
    if not settings.fleet_enabled or not settings.fleet_montage_hub:
        return {"ok": False, "reason": "fleet montage hub disabled"}
    if (settings.fleet_role or "hub").lower() != "hub":
        return {"ok": False, "reason": "not hub role"}

    async with session_scope() as session:
        node = (
            await session.execute(
                select(FleetNode).where(FleetNode.name == node_name)
            )
        ).scalar_one_or_none()
    if node is None:
        return {"ok": False, "reason": f"node {node_name!r} not registered"}
    if node.status not in {FleetNodeStatus.online, FleetNodeStatus.busy}:
        return {"ok": False, "reason": f"node {node_name!r} is {node.status.value}"}

    pulled = await _import_bundle_from_node(
        node,
        project_id=project_id,
        slug=slug,
        ready_at=ready_at,
        skip_if_pulled=not force,
    )
    return {"ok": pulled, "pulled": pulled, "node": node_name, "project_id": project_id}


async def _pull_once() -> None:
    """Не более одного handoff-проекта за цикл — без цепочки #17 → #16 → …"""
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

    candidates: list[tuple[str, dict, FleetNode]] = []
    for node in nodes:
        if node.status not in {FleetNodeStatus.online, FleetNodeStatus.busy}:
            continue
        token = node.token or settings.fleet_agent_token
        try:
            data = await agent_get(node.base_url, token, "/api/fleet/local/pipeline")
        except Exception as exc:  # noqa: BLE001
            logger.debug("fleet pull: {} unreachable ({}): {}", node.name, node.base_url, exc)
            continue

        for proj in data.get("projects") or []:
            if not _agent_project_eligible_for_pull(proj):
                continue
            pid = proj.get("id")
            if not pid:
                continue
            candidates.append((str(proj.get("montage_ready_at") or ""), proj, node))

    if not candidates:
        return

    candidates.sort(key=lambda x: x[0])
    _, proj, node = candidates[0]
    pid = int(proj["id"])
    slug = proj.get("slug") or ""
    ready_at = str(proj.get("montage_ready_at") or "").strip()
    logger.info(
        "fleet auto-pull: one project {}#{} ({} eligible, rest wait)",
        node.name,
        pid,
        len(candidates),
    )
    await _import_bundle_from_node(
        node,
        project_id=pid,
        slug=slug,
        ready_at=ready_at,
    )


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
        logger.info("fleet auto-pull disabled (FLEET_AUTO_PULL=false — только push / «На монтаж»)")
        return
    if _pull_task and not _pull_task.done():
        return
    _pull_task = asyncio.create_task(_pull_loop())
    logger.info("fleet auto-pull loop started (max 1 handoff project per 45s)")
