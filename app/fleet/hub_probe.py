"""Hub: автопроверка доступности воркеров и синхронизация manifest."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger
from sqlalchemy import select

from app.db import session_scope
from app.fleet.client import FleetAgentError, ping_agent
from app.fleet.self_node import is_local_fleet_node, self_node_name
from app.models import FleetNode, FleetNodeStatus
from app.project_root import find_project_root
from app.settings import settings

_probe_task: asyncio.Task | None = None
_HEARTBEAT_GRACE_SEC = 120


def _heartbeat_recent(node: FleetNode) -> bool:
    if not node.last_seen:
        return False
    seen = node.last_seen
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - seen).total_seconds()
    return age <= _HEARTBEAT_GRACE_SEC


def _is_hub() -> bool:
    if not settings.fleet_enabled:
        return False
    role = (settings.fleet_role or "hub").strip().lower()
    return role == "hub" or bool(settings.fleet_montage_hub)


async def probe_fleet_node(node: FleetNode) -> dict:
    """Hub → agent ping; обновляет meta hub_reachable / hub_probe_error."""
    if is_local_fleet_node(node):
        from app.web.studio_version import read_studio_version

        ver = read_studio_version()
        return {
            "ok": True,
            "local": True,
            "info": {
                "name": node.name,
                "hostname": node.hostname,
                "studio_version": ver.get("label") or str(ver.get("version")),
            },
        }

    base_url = (node.base_url or "").strip().rstrip("/")
    if not base_url:
        err = "empty base_url"
        meta = dict(node.meta or {})
        meta["hub_reachable"] = False
        meta["hub_probe_error"] = err
        meta["hub_probe_at"] = datetime.now(timezone.utc).isoformat()
        node.meta = meta
        if not _heartbeat_recent(node):
            node.status = FleetNodeStatus.offline
        return {"ok": False, "error": err}

    token = node.token or settings.fleet_agent_token
    try:
        info = await ping_agent(base_url, token)
    except FleetAgentError as exc:
        info = None
        err = exc.detail[:300]
    except Exception as exc:  # noqa: BLE001
        info = None
        err = str(exc)[:300]
    else:
        err = ""

    meta = dict(node.meta or {})
    meta["hub_probe_at"] = datetime.now(timezone.utc).isoformat()
    if info:
        meta["hub_reachable"] = True
        meta.pop("hub_probe_error", None)
        node.status = FleetNodeStatus.online
        node.last_seen = datetime.now(timezone.utc)
        node.hostname = info.get("hostname") or node.hostname
        node.pipeline_version = info.get("studio_version") or node.pipeline_version
        if info.get("name") and info["name"] != node.name:
            meta["agent_name"] = info["name"]
    else:
        meta["hub_reachable"] = False
        meta["hub_probe_error"] = err or "unreachable"
        if not _heartbeat_recent(node):
            node.status = FleetNodeStatus.offline

    node.meta = meta
    return {"ok": bool(info), "info": info, "error": err or None}


async def sync_fleet_node_by_id(node_id: int) -> dict:
    async with session_scope() as session:
        node = await session.get(FleetNode, node_id)
        if node is None:
            return {"ok": False, "error": "node not found"}
        result = await probe_fleet_node(node)
        await session.commit()
        return result


async def sync_all_fleet_nodes() -> dict:
    async with session_scope() as session:
        rows = (await session.execute(select(FleetNode).order_by(FleetNode.name))).scalars().all()
        results: list[dict] = []
        ok_count = 0
        for node in rows:
            r = await probe_fleet_node(node)
            results.append({"id": node.id, "name": node.name, **r})
            if r.get("ok"):
                ok_count += 1
        await session.commit()
        return {"ok": True, "total": len(results), "reachable": ok_count, "nodes": results}


async def ensure_manifest_workers() -> None:
    """Добавить воркеров из fleet/manifest.json, если их ещё нет."""
    if not _is_hub():
        return
    manifest_path = find_project_root() / "fleet" / "manifest.json"
    if not manifest_path.is_file():
        return
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("fleet manifest read failed: {}", exc)
        return

    workers = data.get("workers") or []
    if not isinstance(workers, list):
        return

    async with session_scope() as session:
        for w in workers:
            if not isinstance(w, dict):
                continue
            name = str(w.get("name") or "").strip()
            url = str(w.get("studio_url") or "").strip().rstrip("/")
            if not name or not url:
                ip = str(w.get("tailscale_ip") or "").strip()
                port = str((data.get("worker_defaults") or {}).get("studio_port") or "8765")
                if name and ip:
                    url = f"http://{ip}:{port}"
            if not name or not url:
                continue
            node = (
                await session.execute(select(FleetNode).where(FleetNode.name == name))
            ).scalar_one_or_none()
            if node is None:
                node = FleetNode(
                    name=name,
                    base_url=url,
                    token=settings.fleet_agent_token or "",
                    is_main=False,
                    role="agent",
                    status=FleetNodeStatus.offline,
                )
                session.add(node)
                logger.info("fleet: added worker from manifest: {} @ {}", name, url)
            elif not (node.base_url or "").strip() or "127.0.0.1" in node.base_url:
                node.base_url = url
                logger.info("fleet: set worker URL from manifest: {} → {}", name, url)
            elif node.base_url.rstrip("/") != url and not _heartbeat_recent(node):
                node.base_url = url
                logger.info("fleet: updated stale worker URL: {} → {}", name, url)
        await session.commit()


def pick_preferred_worker_node_id(nodes: list) -> int | None:
    """Лучший удалённый воркер для автовыбора в UI."""
    self_name = self_node_name()
    manifest_names: list[str] = []
    manifest_path = find_project_root() / "fleet" / "manifest.json"
    if manifest_path.is_file():
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            for w in data.get("workers") or []:
                if isinstance(w, dict) and w.get("name"):
                    manifest_names.append(str(w["name"]))
        except Exception:  # noqa: BLE001
            pass

    def score(n) -> tuple:
        meta = n.meta or {} if hasattr(n, "meta") else {}
        reachable = bool(meta.get("hub_reachable"))
        is_remote = not (
            n.is_main or n.name == self_name or "hub" in (n.role or "")
        )
        in_manifest = n.name in manifest_names
        manifest_rank = manifest_names.index(n.name) if in_manifest else 99
        online = (n.status.value if hasattr(n.status, "value") else str(n.status)) == "online"
        return (
            0 if is_remote and reachable else 1,
            0 if in_manifest else 1,
            manifest_rank,
            0 if reachable else 1,
            0 if online else 1,
            n.name,
        )

    candidates = [
        n
        for n in nodes
        if not (n.is_main or n.name == self_name or "hub" in (n.role or ""))
    ]
    if not candidates:
        return None
    best = min(candidates, key=score)
    meta = best.meta or {}
    if meta.get("hub_reachable") or best.status == FleetNodeStatus.online:
        return best.id
    return best.id


async def _probe_loop() -> None:
    await asyncio.sleep(5)
    while True:
        try:
            if _is_hub():
                await ensure_manifest_workers()
                summary = await sync_all_fleet_nodes()
                logger.debug(
                    "fleet hub probe: {}/{} reachable",
                    summary.get("reachable"),
                    summary.get("total"),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("fleet hub probe loop error: {}", exc)
        await asyncio.sleep(30)


def start_fleet_hub_probe_loop() -> None:
    global _probe_task
    if not _is_hub():
        return
    if _probe_task and not _probe_task.done():
        return
    _probe_task = asyncio.create_task(_probe_loop())
    logger.info("fleet hub probe loop started (auto-connect every 30s)")
