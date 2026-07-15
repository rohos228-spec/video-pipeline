"""Fleet quick-connect from hub."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.web.api import create_app


@pytest.mark.asyncio
async def test_quick_connect_rejects_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.web.routers.fleet.settings.fleet_agent_token", "")
    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            "/api/fleet/nodes/quick-connect",
            json={"base_url": "http://127.0.0.1:8765"},
        )
        assert res.status_code == 400
