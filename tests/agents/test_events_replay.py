"""Reconnect-replay по Last-Event-ID: token из кольца, укрупнённые из БД."""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from fastapi import FastAPI
from sse_starlette.sse import AppStatus

from app.orchestrator.auth import OrchestratorAuthMiddleware
from app.orchestrator.contracts import TOKEN_RING_SIZE
from app.orchestrator.events import persisted_types
from app.web.routers.orchestrator_chat import router
from tests.agents.conftest import TEST_TOKEN


def _chat_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(OrchestratorAuthMiddleware)
    app.include_router(router, prefix="/api")
    return app


async def _read_sse_until_id(app: FastAPI, path: str, headers: list[tuple[bytes, bytes]]) -> bytes:
    """Прочитать тело SSE до первого события и отменить соединение.

    httpx ASGI-транспорт ждёт конец ответа, а поток бесконечный.
    """
    body = bytearray()
    seen = asyncio.Event()
    first_request = True

    async def receive() -> dict[str, object]:
        nonlocal first_request
        if first_request:
            first_request = False
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Future()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        if message["type"] == "http.response.start" and message.get("status") != 200:
            raise AssertionError(message.get("status"))
        if message["type"] == "http.response.body":
            body.extend(message.get("body") or b"")
            if b"event: message" in body:
                seen.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(seen.wait(), timeout=3)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    return bytes(body)


@pytest.mark.asyncio
async def test_stream_ends_when_client_disconnects(monkeypatch: pytest.MonkeyPatch, runtime) -> None:
    del runtime
    monkeypatch.setenv("ORCH_API_TOKEN", TEST_TOKEN)
    app = _chat_app()
    path = "/api/orchestrator/chat/s-disc/stream"
    headers = [
        (b"authorization", f"Bearer {TEST_TOKEN}".encode()),
    ]

    async def receive() -> dict[str, object]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        del message

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("test", 80),
    }
    try:
        await asyncio.wait_for(app(scope, receive, send), timeout=3)
    finally:
        AppStatus.should_exit_event = None


@pytest.mark.asyncio
async def test_replay_skips_last_event_id_and_keeps_tokens_out_of_db(runtime) -> None:
    session = "replay-1"
    await runtime.bus.publish(session, "ack", {"text": "a"})
    await runtime.bus.publish(session, "classification", {"mode": "append"})
    token = await runtime.bus.publish(session, "token", {"text": "ток"})
    checkpoint = await runtime.bus.publish(session, "checkpoint", {"position": 1})
    replayed = runtime.bus.replay(session, token.seq - 1)
    assert [event.seq for event in replayed] == [token.seq, checkpoint.seq]
    assert [event.type for event in replayed] == ["token", "checkpoint"]
    assert "token" not in persisted_types(session)
    assert persisted_types(session) == ["ack", "classification", "checkpoint"]


@pytest.mark.asyncio
async def test_http_stream_replays_after_last_event_id(monkeypatch: pytest.MonkeyPatch, runtime) -> None:
    monkeypatch.setenv("ORCH_API_TOKEN", TEST_TOKEN)
    session = "replay-http"
    first = await runtime.bus.publish(session, "ack", {"text": "старое"})
    await runtime.bus.publish(session, "message", {"role": "assistant", "text": "новое"})
    raw = await _read_sse_until_id(
        _chat_app(),
        f"/api/orchestrator/chat/{session}/stream",
        [
            (b"authorization", f"Bearer {TEST_TOKEN}".encode()),
            (b"last-event-id", str(first.seq).encode()),
        ],
    )
    text = raw.decode()
    assert "event: ack" not in text
    assert "event: message" in text
    assert f"id: {first.seq}" not in text
    assert "новое" in text


@pytest.mark.asyncio
async def test_token_ring_drops_oldest(runtime) -> None:
    session = "replay-ring"
    for index in range(TOKEN_RING_SIZE + 1):
        await runtime.bus.publish(session, "token", {"text": str(index)})
    ring = runtime.bus._rings[session]
    assert len(ring) == TOKEN_RING_SIZE
    assert ring[0].payload["text"] == "1"
    assert ring[-1].payload["text"] == str(TOKEN_RING_SIZE)
    assert persisted_types(session) == []
