"""Heartbeat pipeline cache on hub."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.web.api import create_app


@pytest.mark.asyncio
async def test_register_stores_pipeline_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.fleet.self_node.settings.fleet_node_name", "main-pc")
    app = create_app()
    projects = [{"id": 1, "slug": "test-v1", "status": "music_ready"}]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg = await ac.post(
            "/api/fleet/register",
            json={
                "name": "child-pc",
                "base_url": "http://127.0.0.1:8765",
                "hostname": "child",
                "role": "agent",
                "is_main": False,
                "projects": projects,
            },
        )
        assert reg.status_code == 200
        node_id = reg.json()["id"]

        pipe = await ac.get(f"/api/fleet/nodes/{node_id}/pipeline")
        assert pipe.status_code == 200
        data = pipe.json()
        assert data.get("cached") is True
        assert len(data.get("projects") or []) == 1


@pytest.mark.asyncio
async def test_register_without_token_when_hub_token_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.web.routers.fleet.settings.fleet_agent_token", "secret")
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            "/api/fleet/register",
            json={
                "name": "worker-1",
                "base_url": "http://127.0.0.1:8765",
                "role": "agent",
                "projects": [],
            },
        )
        assert res.status_code == 200
