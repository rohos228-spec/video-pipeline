"""Local fleet node identity on this machine."""

from __future__ import annotations

import platform

from sqlalchemy import select

from app.db import session_scope
from app.models import FleetNode, FleetNodeStatus
from app.settings import settings


def self_node_name() -> str:
    return (settings.fleet_node_name or platform.node() or "local").strip()


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
