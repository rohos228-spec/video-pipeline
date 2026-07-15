"""Local fleet node identity on this machine."""

from __future__ import annotations

import platform
import urllib.parse

from sqlalchemy import select

from app.db import session_scope
from app.models import FleetNode, FleetNodeStatus
from app.settings import settings


def self_node_name() -> str:
    return (settings.fleet_node_name or platform.node() or "local").strip()


def is_localhost_fleet_url(url: str) -> bool:
    low = (url or "").strip().lower()
    return "127.0.0.1" in low or "localhost" in low


def resolve_agent_public_url(
    declared_url: str,
    *,
    remote_host: str | None,
    default_port: int | None = None,
) -> str:
    """Agent шлёт 127.0.0.1 — hub подставляет IP из входящего heartbeat."""
    declared = (declared_url or "").strip().rstrip("/")
    if not is_localhost_fleet_url(declared):
        return declared
    host = (remote_host or "").strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    if not host or is_localhost_fleet_url(f"http://{host}/"):
        return declared
    port = default_port
    if port is None:
        parsed = urllib.parse.urlparse(
            declared if "://" in declared else f"http://{declared}"
        )
        port = parsed.port or settings.web_port
    return f"http://{host}:{port}"


def is_local_fleet_node(node: FleetNode) -> bool:
    """True только для станции с FLEET_NODE_NAME этой машины.

    Сравнение по base_url ломалось, когда FLEET_PUBLIC_URL не задан:
    hub и agent оба регистрировались как http://127.0.0.1:8765.
    """
    return node.name == self_node_name()


async def ensure_self_fleet_node() -> None:
    if not settings.fleet_enabled:
        return
    name = self_node_name()
    base = settings.fleet_agent_base_url.rstrip("/")
    async with session_scope() as session:
        row = (
            await session.execute(select(FleetNode).where(FleetNode.name == name))
        ).scalar_one_or_none()
        if row is None:
            row = FleetNode(
                name=name,
                base_url=base,
                token=settings.fleet_agent_token or "",
                is_main=settings.fleet_is_main,
                role=settings.fleet_role or "hub",
                status=FleetNodeStatus.online,
                hostname=platform.node(),
            )
            session.add(row)
        else:
            row.base_url = base
            row.is_main = settings.fleet_is_main
            row.role = settings.fleet_role or row.role
            row.status = FleetNodeStatus.online
            row.hostname = platform.node()
        await session.commit()


async def ensure_hub_peer_node() -> None:
    """Agent: hub в локальной БД, чтобы «Сеть» показывала проекты главного ПК."""
    if not settings.fleet_enabled:
        return
    role = (settings.fleet_role or "hub").strip().lower()
    if role != "agent":
        return
    hub_url = (settings.fleet_hub_url or "").strip().rstrip("/")
    if not hub_url or is_localhost_fleet_url(hub_url):
        return
    hub_name = "hub"
    async with session_scope() as session:
        row = (
            await session.execute(select(FleetNode).where(FleetNode.name == hub_name))
        ).scalar_one_or_none()
        if row is None:
            row = FleetNode(
                name=hub_name,
                base_url=hub_url,
                token=settings.fleet_agent_token or "",
                is_main=True,
                role="hub",
                status=FleetNodeStatus.online,
                hostname="hub",
            )
            session.add(row)
        else:
            row.base_url = hub_url
            row.is_main = True
            row.role = "hub"
            row.status = FleetNodeStatus.online
        await session.commit()
