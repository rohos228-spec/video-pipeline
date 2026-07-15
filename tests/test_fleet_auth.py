"""Fleet local endpoints — open on private Tailscale LAN (no agent bearer)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.web.api import create_app

app = create_app()


@pytest.mark.asyncio
async def test_local_pipeline_no_auth_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.web.routers.fleet.settings.fleet_agent_token", "secret-token")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/fleet/local/pipeline")
        assert res.status_code == 200
        assert "projects" in res.json()
