"""401 без токена на /api/orchestrator/**."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.orchestrator.auth import OrchestratorAuthMiddleware
from app.web.api import create_app
from app.web.routers.orchestrator_chat import router
from tests.agents.conftest import TEST_TOKEN


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OrchestratorAuthMiddleware)
    app.include_router(router, prefix="/api")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


@pytest.mark.asyncio
async def test_missing_or_bad_token_is_401(monkeypatch: pytest.MonkeyPatch, runtime) -> None:
    del runtime
    monkeypatch.setenv("ORCH_API_TOKEN", TEST_TOKEN)
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        missing = await client.post(
            "/api/orchestrator/chat/s1/message",
            json={"text": "привет"},
        )
        wrong = await client.post(
            "/api/orchestrator/chat/s1/message",
            json={"text": "привет"},
            headers={"Authorization": "Bearer not-the-token"},
        )
        stream = await client.get("/api/orchestrator/chat/s1/stream")
        health = await client.get("/api/health")
    assert missing.status_code == 401
    assert wrong.status_code == 401
    assert stream.status_code == 401
    assert health.status_code == 200
    assert "ORCH_API_TOKEN" not in missing.text
    assert TEST_TOKEN not in missing.text


@pytest.mark.asyncio
async def test_empty_env_rejects_any_bearer(monkeypatch: pytest.MonkeyPatch, runtime) -> None:
    del runtime
    monkeypatch.setenv("ORCH_API_TOKEN", "")
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/orchestrator/chat/s1/message",
            json={"text": "привет"},
            headers={"Authorization": "Bearer something"},
        )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_valid_token_accepts_message(monkeypatch: pytest.MonkeyPatch, runtime) -> None:
    monkeypatch.setenv("ORCH_API_TOKEN", TEST_TOKEN)
    runtime.router.answer = "принято"
    transport = ASGITransport(app=_app())
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/orchestrator/chat/s1/message",
            json={"text": "который час?"},
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["ack_seq"] >= 1
    assert TEST_TOKEN not in response.text


def _included_paths(app: FastAPI) -> set[str]:
    paths: set[str] = set()
    for route in app.routes:
        ctx = getattr(route, "include_context", None)
        if ctx is None:
            path = getattr(route, "path", None)
            if path:
                paths.add(path)
            continue
        prefix = ctx.prefix or ""
        for inner in ctx.included_router.routes:
            paths.add(prefix + getattr(inner, "path", ""))
    return paths


def test_create_app_mounts_chat_and_middleware() -> None:
    app = create_app()
    paths = _included_paths(app)
    assert "/api/orchestrator/chat/{session}/message" in paths
    assert "/api/orchestrator/chat/{session}/stream" in paths
    assert OrchestratorAuthMiddleware in [item.cls for item in app.user_middleware]
