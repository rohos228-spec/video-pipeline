"""Fleet agent bearer auth on local endpoints."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.web.api import create_app

app = create_app()


@pytest.mark.asyncio
async def test_local_pipeline_accepts_bearer_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.web.routers.fleet.settings.fleet_agent_token", "secret-token")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        bad = await ac.get("/api/fleet/local/pipeline")
        assert bad.status_code == 401
        ok = await ac.get(
            "/api/fleet/local/pipeline",
            headers={"Authorization": "Bearer secret-token"},
        )
        assert ok.status_code == 200
        body = ok.json()
        assert "projects" in body
