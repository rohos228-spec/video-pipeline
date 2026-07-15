"""End-to-end: queue pull → register delivers → (agent would push)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.web.api import create_app


@pytest.mark.asyncio
async def test_pull_register_deliver_chain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.fleet.self_node.settings.fleet_node_name", "main-pc")
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg = await ac.post(
            "/api/fleet/register",
            json={
                "name": "child-pc",
                "base_url": "http://127.0.0.1:8765",
                "role": "agent",
                "projects": [{"id": 5, "slug": "test-v1", "status": "music_ready"}],
            },
        )
        nid = reg.json()["id"]
        pull = await ac.post(
            f"/api/fleet/nodes/{nid}/projects/5/pull-to-main",
            json={"run_assemble": True, "slug": "test-v1"},
        )
        assert pull.json().get("pending") is True

        reg2 = await ac.post(
            "/api/fleet/register",
            json={"name": "child-pc", "base_url": "http://127.0.0.1:8765", "role": "agent"},
        )
        actions = reg2.json().get("pending_actions") or []
        assert len(actions) == 1
        assert actions[0]["project_id"] == 5
        assert actions[0].get("slug") == "test-v1"

        diag = await ac.get("/api/fleet/diagnostics")
        assert diag.status_code == 200
