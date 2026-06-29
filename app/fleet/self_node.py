"""Регистрация этого ПК в fleet (hub + worker на одной машине)."""

from __future__ import annotations

import platform
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select

from app.db import session_scope
from app.models import FleetNode, FleetNodeStatus
from app.settings import settings


def self_node_name() -> str:
    if settings.fleet_node_name.strip():
        return settings.fleet_node_name.strip()
    return platform.node()


def is_local_fleet_node(node: FleetNode) -> bool:
    """Эта станция = текущий процесс Studio."""
    if node.is_main and settings.fleet_montage_hub:
        return True
    local_name = self_node_name()
    if node.name == local_name:
        return True
    return node.base_url.rstrip("/").lower() == settings.fleet_agent_base_url.rstrip("/").lower()


async def ensure_self_fleet_node() -> None:
    if not settings.fleet_enabled:
        return

    name = self_node_name()
    base_url = settings.fleet_agent_base_url
    is_main = bool(settings.fleet_montage_hub or settings.fleet_is_main)
    role = settings.fleet_role or "hub"
    if role.lower() == "hub" and settings.fleet_hub_is_worker:
        role = "hub+worker"

    async with session_scope() as session:
        node = (
            await session.execute(select(FleetNode).where(FleetNode.name == name))
        ).scalar_one_or_none()
        if node is None:
            node = FleetNode(
                name=name,
                base_url=base_url,
                token=settings.fleet_agent_token or "",
                is_main=is_main,
                role=role,
            )
            session.add(node)
        else:
            node.base_url = base_url
            node.token = settings.fleet_agent_token or node.token or ""
            node.is_main = is_main
            node.role = role
        node.status = FleetNodeStatus.online
        node.last_seen = datetime.now(timezone.utc)
        node.hostname = platform.node()
        await session.commit()

    logger.info("fleet self-node registered: {} ({}) @ {}", name, role, base_url)
