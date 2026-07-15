"""Stale fleet node cleanup."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.web.api import create_app


@pytest.mark.asyncio
async def test_prune_stale_removes_old_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime, timedelta, timezone

    from app.db import session_scope
    from app.models import FleetNode, FleetNodeStatus

    monkeypatch.setattr("app.fleet.self_node.settings.fleet_node_name", "main-pc")
    app = create_app()
    async with session_scope() as session:
        session.add(
            FleetNode(
                name="ghost-pc",
                base_url="http://127.0.0.1:8765",
                role="agent",
                status=FleetNodeStatus.offline,
                last_seen=datetime.now(timezone.utc) - timedelta(days=30),
            )
        )
        await session.commit()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post("/api/fleet/nodes/prune-stale")
        assert res.status_code == 200
        assert "ghost-pc" in res.json().get("pruned", [])
