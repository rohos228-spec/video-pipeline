"""Приёмка R6: живой HTTP+SSE на 127.0.0.1:8877, без сети и без мока действий."""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from sse_starlette.sse import AppStatus

from app.orchestrator.agents.runtime import get_runtime, set_runtime
from app.orchestrator.agents.stubs import FallbackRouter, StubPlanner, UnlinkedActions
from app.orchestrator.agents.testkit import (
    MEDIUM_ACTION,
    PHRASE_MEDIUM,
    R6_PORT,
    R6Router,
    build_r6_app,
    build_r6_runtime,
    enforce_r6_gate,
)
from app.web.api import _lifespan
from tests.agents.conftest import TEST_TOKEN

_AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCH_RUNTIME", raising=False)
    monkeypatch.delenv("ORCH_TEST", raising=False)
    monkeypatch.delenv("ORCH_TEST_STEP_DELAY_S", raising=False)


async def _wait_port(port: int) -> None:
    for _ in range(50):
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            await asyncio.sleep(0.05)
            continue
        writer.close()
        await writer.wait_closed()
        return
    raise RuntimeError(f"порт {port} не открылся")


@pytest.fixture
async def r6_api(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    monkeypatch.setenv("ORCH_RUNTIME", "r6-fixture")
    monkeypatch.setenv("ORCH_TEST", "1")
    monkeypatch.setenv("ORCH_TEST_STEP_DELAY_S", "0.3")
    monkeypatch.setenv("ORCH_API_TOKEN", TEST_TOKEN)
    set_runtime(None)
    AppStatus.should_exit = False
    AppStatus.should_exit_event = None
    app = build_r6_app()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=R6_PORT,
        log_level="warning",
        access_log=False,
        timeout_graceful_shutdown=1,
    )
    server = uvicorn.Server(config)
    server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
    job = asyncio.create_task(server.serve())
    try:
        await _wait_port(R6_PORT)
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{R6_PORT}", timeout=10) as client:
            yield client
    finally:
        server.should_exit = True
        with contextlib.suppress(asyncio.TimeoutError, asyncio.CancelledError, SystemExit):
            await asyncio.wait_for(job, timeout=5)
        if not job.done():
            job.cancel()
            with contextlib.suppress(asyncio.CancelledError, SystemExit):
                await job
        set_runtime(None)
        AppStatus.should_exit = False
        AppStatus.should_exit_event = None


async def _events_until(
    client: httpx.AsyncClient,
    session: str,
    predicate,
    on_events=None,
    timeout: float = 8.0,
) -> list[dict]:
    found: list[dict] = []
    async with client.stream(
        "GET",
        f"/api/orchestrator/chat/{session}/stream",
        headers=_AUTH,
    ) as response:
        assert response.status_code == 200
        current: dict[str, str] = {"event": "", "data": ""}

        async def _pump() -> None:
            async for line in response.aiter_lines():
                if line.startswith("event:"):
                    current["event"] = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    current["data"] += line.split(":", 1)[1].strip()
                elif line == "" and current["event"]:
                    payload = json.loads(current["data"]) if current["data"] else {}
                    found.append({"event": current["event"], "payload": payload.get("payload", payload)})
                    current["event"] = ""
                    current["data"] = ""
                    if on_events is not None:
                        await on_events(found)
                    if predicate(found):
                        return

        await asyncio.wait_for(_pump(), timeout)
    return found


def test_production_runtime_ignores_missing_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ORCH_TEST", "1")
    set_runtime(None)
    try:
        enforce_r6_gate()
        runtime = get_runtime()
        assert isinstance(runtime.router, FallbackRouter)
        assert isinstance(runtime.supervisor.planner, StubPlanner)
        assert isinstance(runtime.supervisor.actions, UnlinkedActions)
    finally:
        set_runtime(None)


def test_r6_fixture_without_orch_test_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ORCH_RUNTIME", "r6-fixture")
    set_runtime(None)
    try:
        with pytest.raises(RuntimeError, match="ORCH_TEST"):
            enforce_r6_gate()
        with pytest.raises(RuntimeError, match="ORCH_TEST"):
            build_r6_runtime()
        with pytest.raises(RuntimeError, match="ORCH_TEST"):
            get_runtime()
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_r6_router_requires_run_id_and_chunks_on_delta() -> None:
    router = R6Router()
    with pytest.raises(ValueError, match="run_id"):
        await router.call("strong", [{"role": "user", "content": "x"}], budget_scope="internal")
    with pytest.raises(ValueError, match="run_id"):
        await router.call(
            "strong",
            [{"role": "user", "content": "x"}],
            budget_scope="pipeline",
            run_id="  ",
        )
    with pytest.raises(ValueError, match="run_id"):
        await router.classify("текст")
    pieces: list[str] = []
    response = await router.call(
        "strong",
        [{"role": "user", "content": "x"}],
        True,
        run_id="orch-session-s",
        budget_scope="pipeline",
        on_delta=pieces.append,
    )
    assert len(pieces) >= 2
    assert "".join(pieces) == response.text


@pytest.mark.asyncio
async def test_lifespan_refuses_r6_without_orch_test(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("ORCH_RUNTIME", "r6-fixture")
    app = FastAPI()
    with pytest.raises(RuntimeError, match="ORCH_TEST"):
        async with _lifespan(app):
            pass


@pytest.mark.asyncio
async def test_r6_steer_checkpoint_and_interrupt_over_http(r6_api: httpx.AsyncClient) -> None:
    steer = "r6-steer"
    posted = await r6_api.post(
        f"/api/orchestrator/chat/{steer}/message",
        headers=_AUTH,
        json={"text": "разложи шаги", "mode": "task", "project_id": 13},
    )
    assert posted.status_code == 200
    steered_once = False

    async def _steer_after_look(items: list[dict]) -> None:
        nonlocal steered_once
        if steered_once:
            return
        if any(item["event"] == "step_started" and item["payload"].get("action") == "test.look" for item in items):
            steered_once = True
            steered = await r6_api.post(
                f"/api/orchestrator/chat/{steer}/message",
                headers=_AUTH,
                json={"text": "измени план", "mode": "steer"},
            )
            assert steered.status_code == 200

    events = await _events_until(
        r6_api,
        steer,
        lambda items: any(
            item["event"] == "step_started" and item["payload"].get("action") == "test.look_again"
            for item in items
        ),
        on_events=_steer_after_look,
    )
    assert steered_once
    assert any(item["event"] == "checkpoint" for item in events)
    assert any(item["event"] == "step_started" and item["payload"].get("action") == "test.look" for item in events)

    stop = "r6-stop"
    posted = await r6_api.post(
        f"/api/orchestrator/chat/{stop}/message",
        headers=_AUTH,
        json={"text": "разложи шаги", "mode": "task", "project_id": 13},
    )
    assert posted.status_code == 200
    stopped_once = False

    async def _cancel_after_look(items: list[dict]) -> None:
        nonlocal stopped_once
        if stopped_once:
            return
        if any(item["event"] == "step_started" and item["payload"].get("action") == "test.look" for item in items):
            stopped_once = True
            cancelled = await r6_api.post(
                f"/api/orchestrator/chat/{stop}/message",
                headers=_AUTH,
                json={"text": "отмена"},
            )
            assert cancelled.status_code == 200

    stopped = await _events_until(
        r6_api,
        stop,
        lambda items: any(
            item["event"] == "agent_status" and item["payload"].get("status") == "cancelled" for item in items
        ),
        on_events=_cancel_after_look,
    )
    assert stopped_once
    assert any(item["event"] == "agent_status" and item["payload"].get("status") == "cancelled" for item in stopped)
    blob = json.dumps(stopped, ensure_ascii=False)
    assert "delete_frame" not in blob
    assert "delete_scene" not in blob


@pytest.mark.asyncio
async def test_r6_medium_touch_asks_approval_over_http(r6_api: httpx.AsyncClient) -> None:
    session = "r6-medium"
    posted = await r6_api.post(
        f"/api/orchestrator/chat/{session}/message",
        headers=_AUTH,
        json={"text": PHRASE_MEDIUM, "mode": "task", "project_id": 13},
    )
    assert posted.status_code == 200
    events = await _events_until(
        r6_api,
        session,
        lambda items: any(
            item["event"] == "approval_required" and item["payload"].get("reason") == "risk" for item in items
        ),
    )
    approval = next(item for item in events if item["event"] == "approval_required")
    assert approval["payload"]["reason"] == "risk"
    assert approval["payload"]["action"] == MEDIUM_ACTION
    assert all(item["payload"].get("action") != MEDIUM_ACTION or item["event"] != "step_started" for item in events)


@pytest.mark.asyncio
async def test_r6_medium_without_project_is_missing_project_id(r6_api: httpx.AsyncClient) -> None:
    session = "r6-nopid"
    posted = await r6_api.post(
        f"/api/orchestrator/chat/{session}/message",
        headers=_AUTH,
        json={"text": PHRASE_MEDIUM, "mode": "task"},
    )
    assert posted.status_code == 200
    events = await _events_until(
        r6_api,
        session,
        lambda items: any(
            item["event"] == "approval_required" and item["payload"].get("reason") == "missing_project_id"
            for item in items
        ),
    )
    assert events[-1]["event"] == "approval_required" or any(
        item["payload"].get("reason") == "missing_project_id" for item in events
    )
    assert not any(item["event"] == "step_started" for item in events)


@pytest.mark.asyncio
async def test_r6_append_emits_token_chunks_over_sse(r6_api: httpx.AsyncClient) -> None:
    session = "r6-tokens"
    posted = await r6_api.post(
        f"/api/orchestrator/chat/{session}/message",
        headers=_AUTH,
        json={"text": "добро пожаловать"},
    )
    assert posted.status_code == 200
    events = await _events_until(
        r6_api,
        session,
        lambda items: sum(1 for item in items if item["event"] == "token") >= 2,
    )
    tokens = [item["payload"].get("text", "") for item in events if item["event"] == "token"]
    assert len(tokens) >= 2
    assert "".join(tokens).strip()


@pytest.mark.asyncio
async def test_r6_kill_sets_stop_key(r6_api: httpx.AsyncClient) -> None:
    session = "r6-kill"
    posted = await r6_api.post(
        f"/api/orchestrator/chat/{session}/message",
        headers=_AUTH,
        json={"text": "разложи шаги", "mode": "task", "project_id": 13},
    )
    assert posted.status_code == 200
    armed = False

    async def _kill_after_look(items: list[dict]) -> None:
        nonlocal armed
        if armed:
            return
        if any(item["event"] == "step_started" and item["payload"].get("action") == "test.look" for item in items):
            armed = True
            killed = await r6_api.post(
                f"/api/orchestrator/chat/{session}/message",
                headers=_AUTH,
                json={"text": "kill switch"},
            )
            assert killed.status_code == 200

    events = await _events_until(
        r6_api,
        session,
        lambda items: any(item["event"] == "kill_switch" for item in items),
        on_events=_kill_after_look,
    )
    assert armed
    kill = next(item for item in events if item["event"] == "kill_switch")
    assert kill["payload"]["stop_key_set"] is True
    assert kill["payload"]["only_node"] is True
    assert kill["payload"].get("mid_flight") is not True
    store = get_runtime().supervisor.node_stops
    assert store.same(session, kill["payload"].get("stop_key"))


@pytest.mark.asyncio
async def test_r6_scene_word_makes_task(r6_api: httpx.AsyncClient) -> None:
    session = "r6-scene"
    posted = await r6_api.post(
        f"/api/orchestrator/chat/{session}/message",
        headers=_AUTH,
        json={"text": "разбери сцен"},
    )
    assert posted.status_code == 200
    events = await _events_until(
        r6_api,
        session,
        lambda items: any(
            item["event"] == "step_started" and item["payload"].get("action") == "test.look" for item in items
        ),
    )
    classified = next(item for item in events if item["event"] == "classification")
    assert classified["payload"]["mode"] == "task"
    started = next(
        item for item in events if item["event"] == "step_started" and item["payload"].get("action") == "test.look"
    )
    assert started["payload"]["paid"] is False
    assert not any(item["event"] == "token" for item in events)


def _classification(runtime, session: str) -> dict:
    found = [event.payload for event in runtime.bus.replay(session, 0) if event.type == "classification"]
    assert found, session
    return found[-1]


@pytest.mark.asyncio
async def test_prod_ignores_x_orch_scenario(monkeypatch: pytest.MonkeyPatch) -> None:
    """Прод-роутер не смотрит ``X-Orch-Scenario`` и ``?scenario=``.

    ``ORCH_TEST=1`` без ``ORCH_RUNTIME=r6-fixture`` сценарии testkit не включает.
    Заголовок, которым WS4 выбирает мок, не меняет классификацию и не ставит
    kill, approval или шаг ``test.medium_touch``.
    """
    _clear_env(monkeypatch)
    monkeypatch.setenv("ORCH_TEST", "1")
    monkeypatch.setenv("ORCH_API_TOKEN", TEST_TOKEN)
    set_runtime(None)
    text = "добрый день"
    scenarios = (
        ("kill", "prod-kill"),
        ("interrupt", "prod-interrupt"),
        ("approval", "prod-approval"),
        ("killmsg", "prod-killmsg"),
        ("budget", "prod-budget"),
        ("medium", "prod-medium"),
    )
    try:
        runtime = get_runtime()
        assert isinstance(runtime.router, FallbackRouter)
        assert not isinstance(runtime.router, R6Router)
        assert isinstance(runtime.supervisor.actions, UnlinkedActions)
        transport = httpx.ASGITransport(app=build_r6_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            plain = await client.post(
                "/api/orchestrator/chat/prod-plain/message",
                json={"text": text},
                headers=_AUTH,
            )
            assert plain.status_code == 200
            for scenario, session in scenarios:
                response = await client.post(
                    f"/api/orchestrator/chat/{session}/message",
                    params={"scenario": scenario},
                    json={"text": text},
                    headers={**_AUTH, "X-Orch-Scenario": scenario},
                )
                assert response.status_code == 200, scenario
            phrase = await client.post(
                "/api/orchestrator/chat/prod-phrase/message",
                params={"scenario": PHRASE_MEDIUM},
                json={"text": PHRASE_MEDIUM},
                headers={**_AUTH, "X-Orch-Scenario": "approval"},
            )
            assert phrase.status_code == 200
        await runtime.queue.drain(timeout=3)
        baseline = _classification(runtime, "prod-plain")
        assert baseline["mode"] == "append"
        assert baseline["kill_switch"] is False
        for _scenario, session in scenarios:
            assert _classification(runtime, session) == baseline
            kinds = {event.type for event in runtime.bus.replay(session, 0)}
            assert "kill_switch" not in kinds
            assert "approval_required" not in kinds
            assert "step_started" not in kinds
        phrase_cls = _classification(runtime, "prod-phrase")
        assert phrase_cls["mode"] == "append"
        assert phrase_cls["kill_switch"] is False
        phrase_kinds = {event.type for event in runtime.bus.replay("prod-phrase", 0)}
        assert "approval_required" not in phrase_kinds
        assert "step_started" not in phrase_kinds
        assert MEDIUM_ACTION not in json.dumps(
            [event.payload for event in runtime.bus.replay("prod-phrase", 0)],
            ensure_ascii=False,
        )
    finally:
        set_runtime(None)
