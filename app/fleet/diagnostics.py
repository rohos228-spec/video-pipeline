"""Fleet health + cleanup stale ghost nodes."""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import delete, select

from app.db import session_scope
from app.fleet.self_node import is_localhost_fleet_url, self_node_name
from app.models import FleetNode
from app.settings import settings

STALE_NODE_MAX_AGE_SEC = 3600
HEARTBEAT_WARN_AGE_SEC = 120


def _utc_age_sec(ts: datetime | None) -> float | None:
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - ts).total_seconds()


async def prune_stale_fleet_nodes(*, max_age_sec: int = STALE_NODE_MAX_AGE_SEC) -> list[str]:
    """Удалить agent-узлы без heartbeat дольше max_age_sec (не трогаем self hub)."""
    self_name = self_node_name()
    removed: list[str] = []
    async with session_scope() as session:
        rows = (await session.execute(select(FleetNode))).scalars().all()
        for n in rows:
            if n.name == self_name:
                continue
            age = _utc_age_sec(n.last_seen)
            if age is None or age > max_age_sec:
                removed.append(n.name)
        if removed:
            await session.execute(delete(FleetNode).where(FleetNode.name.in_(removed)))
            await session.commit()
            logger.info("fleet: удалены мёртвые станции: {}", ", ".join(removed))
    return removed


def fleet_setup_status() -> dict:
    """Человекочитаемый статус настройки fleet для UI."""
    enabled = settings.fleet_enabled
    role = (settings.fleet_role or "hub").strip().lower()
    hub_url = (settings.fleet_hub_url or "").strip()
    pub = (settings.fleet_public_url or settings.fleet_agent_base_url or "").strip()
    agent_ready = enabled and role == "agent" and bool(hub_url) and not is_localhost_fleet_url(hub_url)
    hub_ready = enabled and role == "hub"
    steps: list[str] = []
    if not enabled:
        steps.append("В .env задайте FLEET_ENABLED=true и перезапустите Studio")
    elif role == "agent":
        if not hub_url:
            steps.append("Запустите scripts/fleet-setup.ps1 или задайте FLEET_HUB_URL=http://<tailscale-ip-hub>:8765")
        elif is_localhost_fleet_url(hub_url):
            steps.append("FLEET_HUB_URL не должен быть localhost — укажите Tailscale IP главного ПК")
        else:
            steps.append("На этом ПК: FLEET_ROLE=agent, git pull origin main, перезапуск Studio")
            steps.append("В логе должно быть: fleet agent heartbeat ok: N projects → hub")
    else:
        steps.append("На дочернем ПК: scripts/fleet-setup.ps1 (роль agent) + перезапуск")
        steps.append("Здесь: дождитесь heartbeat <120s или нажмите Sync в панели «Сеть»")
    return {
        "fleet_enabled": enabled,
        "role": role,
        "hub_url_set": bool(hub_url),
        "public_url_set": bool(pub) and not is_localhost_fleet_url(pub),
        "agent_ready": agent_ready,
        "hub_ready": hub_ready,
        "needs_setup": not (agent_ready if role == "agent" else hub_ready),
        "steps": steps,
    }


async def build_fleet_diagnostics(*, prune: bool = True) -> dict:
    pruned: list[str] = []
    if prune and settings.fleet_enabled:
        pruned = await prune_stale_fleet_nodes()

    role = (settings.fleet_role or "hub").strip().lower()
    self_name = self_node_name()
    issues: list[str] = []
    ok: list[str] = []
    setup = fleet_setup_status()

    if pruned:
        ok.append(f"удалено мёртвых станций: {', '.join(pruned)}")

    if not settings.fleet_enabled:
        issues.append("FLEET_ENABLED=false — включите в .env или scripts/fleet-setup.ps1")
    else:
        ok.append("fleet включён")

    if role == "agent":
        hub = (settings.fleet_hub_url or "").strip()
        if not hub:
            issues.append("FLEET_HUB_URL не задан — scripts/fleet-setup.ps1")
        elif is_localhost_fleet_url(hub):
            issues.append(f"FLEET_HUB_URL={hub} — нужен Tailscale IP hub")
        else:
            ok.append(f"hub URL {hub}")
    else:
        if is_localhost_fleet_url(settings.fleet_agent_base_url):
            issues.append("FLEET_PUBLIC_URL не задан (не критично если agent шлёт heartbeat)")

    nodes_out: list[dict] = []
    async with session_scope() as session:
        rows = (await session.execute(select(FleetNode).order_by(FleetNode.name))).scalars().all()
        for n in rows:
            meta = n.meta or {}
            snap = meta.get("pipeline_snapshot") or []
            pending = meta.get("pending_pulls") or []
            last_err = meta.get("last_pull_error")
            age = _utc_age_sec(n.last_seen)
            is_self = n.name == self_name
            node_issues: list[str] = []
            if not is_self and role == "hub":
                if age is None or age > HEARTBEAT_WARN_AGE_SEC:
                    node_issues.append(
                        f"нет heartbeat {int(age) if age is not None else '?'}s — "
                        "на child: FLEET_ROLE=agent, git pull, restart Studio"
                    )
                elif age <= HEARTBEAT_WARN_AGE_SEC:
                    ok.append(f"{n.name}: online ({int(age)}s)")
                if pending:
                    node_issues.append(f"ждёт отправки {len(pending)} pull(s)")
                if last_err:
                    node_issues.append(f"ошибка pull: {last_err}")
            nodes_out.append(
                {
                    "name": n.name,
                    "base_url": n.base_url,
                    "role": n.role,
                    "is_self": is_self,
                    "last_seen_sec_ago": round(age) if age is not None else None,
                    "online": age is not None and age <= HEARTBEAT_WARN_AGE_SEC,
                    "cached_projects": len(snap) if isinstance(snap, list) else 0,
                    "pending_pulls": len(pending) if isinstance(pending, list) else 0,
                    "issues": node_issues,
                }
            )
            issues.extend(f"{n.name}: {x}" for x in node_issues)

    remote = [n for n in nodes_out if not n["is_self"]]
    if role == "hub" and not remote and setup["needs_setup"]:
        issues.append("нет дочерних станций — child: FLEET_ROLE=agent + fleet-setup.ps1 + restart")

    return {
        "ok": len(issues) == 0,
        "role": role,
        "self_node": self_name,
        "pruned": pruned,
        "setup": setup,
        "issues": issues,
        "checks_ok": ok,
        "nodes": nodes_out,
        "fix": (
            "Hub: git pull + перезапуск Studio + панель «Сеть» → диагностика. "
            "Child: scripts/fleet-setup.ps1, FLEET_ROLE=agent, git pull, restart. "
            "«На монтаж» → очередь heartbeat (~5 сек)."
        ),
    }
