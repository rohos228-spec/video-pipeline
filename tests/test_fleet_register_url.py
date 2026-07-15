"""Fleet register must not overwrite Tailscale agent URL with localhost."""

from __future__ import annotations

import pytest

from app.fleet.self_node import is_localhost_fleet_url, resolve_agent_public_url


def test_is_localhost_fleet_url() -> None:
    assert is_localhost_fleet_url("http://127.0.0.1:8765") is True
    assert is_localhost_fleet_url("http://localhost:8765") is True
    assert is_localhost_fleet_url("http://100.100.240.106:8765") is False


def test_resolve_agent_public_url_from_heartbeat_ip() -> None:
    url = resolve_agent_public_url(
        "http://127.0.0.1:8765",
        remote_host="100.100.240.106",
        default_port=8765,
    )
    assert url == "http://100.100.240.106:8765"


def test_resolve_agent_public_url_keeps_declared_tailscale() -> None:
    url = resolve_agent_public_url(
        "http://100.100.240.106:8765",
        remote_host="100.72.202.35",
        default_port=8765,
    )
    assert url == "http://100.100.240.106:8765"


@pytest.mark.asyncio
async def test_register_keeps_tailscale_url_when_agent_sends_localhost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.db import session_scope
    from app.models import FleetNode
    from app.web.api import create_app

    monkeypatch.setattr("app.web.routers.fleet.settings.fleet_agent_token", "tok")
    app = create_app()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(
            "/api/fleet/register",
            json={
                "name": "child-pc",
                "base_url": "http://100.100.240.106:8765",
                "hostname": "child",
                "role": "agent",
                "is_main": False,
            },
            headers={"Authorization": "Bearer tok"},
        )
        assert res.status_code == 200

        res2 = await ac.post(
            "/api/fleet/register",
            json={
                "name": "child-pc",
                "base_url": "http://127.0.0.1:8765",
                "hostname": "child",
                "role": "agent",
                "is_main": False,
            },
            headers={"Authorization": "Bearer tok"},
        )
        assert res2.status_code == 200
        assert res2.json()["base_url"] == "http://100.100.240.106:8765"
