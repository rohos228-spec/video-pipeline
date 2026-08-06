"""Agent loop: tool calls, риск-гейт, лимит итераций, долговечность сессии."""

from __future__ import annotations

import json

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import AgentSessionStatus, Base, Frame, Project, ProjectStatus
from app.services import gpt_api
from app.services.agent import journal, loop, sessions
from app.services.agent import tools_read


@pytest.fixture
async def env(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/agent.db")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    for mod in (sessions, journal, tools_read):
        monkeypatch.setattr(mod, "SessionLocal", factory)
    yield engine, factory
    await engine.dispose()


def _fc_result(name: str, args: dict, call_id: str = "call_1") -> gpt_api.GptChatResult:
    raw = {
        "output": [
            {
                "type": "function_call",
                "id": f"fc_{call_id}",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(args),
            }
        ]
    }
    return gpt_api.GptChatResult(
        text="",
        model="m",
        raw=raw,
        tool_calls=[
            {
                "id": f"fc_{call_id}",
                "call_id": call_id,
                "name": name,
                "arguments": json.dumps(args),
            }
        ],
    )


def _text_result(text: str) -> gpt_api.GptChatResult:
    return gpt_api.GptChatResult(text=text, model="m", raw={"output": []})


@pytest.mark.asyncio
async def test_turn_one_read_tool_then_answer(env, monkeypatch) -> None:
    engine, factory = env
    async with factory() as s:
        s.add(
            Project(
                id=1, slug="p1", topic="t1",
                status=ProjectStatus.generating_videos, auto_mode=True, meta={},
            )
        )
        await s.commit()

    calls: list[list[dict]] = []

    async def fake_chat(**kwargs):
        calls.append(kwargs["input_items"])
        if len(calls) == 1:
            return _fc_result("get_project", {"project_id": 1})
        return _text_result("Проект 1: generating_videos, всё идёт.")

    monkeypatch.setattr(loop.gpt_api, "chat", fake_chat)

    sess = await sessions.create_session(title="t", autonomy="operator")
    events: list[tuple[str, dict]] = []

    async def on_event(kind, data):
        events.append((kind, data))

    res = await loop.run_agent_turn(sess.uuid, "что с проектом 1?", on_event=on_event)
    assert "generating_videos" in res["text"]
    assert res["iterations"] == 2
    kinds = [k for k, _ in events]
    assert "tool_call" in kinds and "tool_result" in kinds and "assistant" in kinds

    # долговечность: input_items содержат echo + function_call_output
    reloaded = await sessions.get_session(sess.id)
    assert reloaded.status == AgentSessionStatus.idle
    assert any(
        it.get("type") == "function_call_output" for it in reloaded.input_items
    )

    # журнал
    actions = await journal.recent_actions(session_id=sess.id)
    assert actions[0]["tool"] == "get_project"
    assert actions[0]["status"] == "ok"
    assert actions[0]["project_id"] == 1

    # сообщения для UI
    msgs = await sessions.load_messages(sess.id)
    roles = [m.role for m in msgs]
    assert roles[0] == "user" and "tool" in roles and roles[-1] == "assistant"


@pytest.mark.asyncio
async def test_turn_denied_above_autonomy(env, monkeypatch) -> None:
    """advisor (только read): write-tool не выполняется, в журнале denied."""
    from app.services.agent import registry

    @registry.tool(
        name="fake_write_step",
        description="w",
        parameters={"type": "object", "properties": {}},
        risk="write",
    )
    async def _fake(args):
        raise AssertionError("не должен выполняться")

    async def fake_chat(**kwargs):
        return _fc_result("fake_write_step", {})

    monkeypatch.setattr(loop.gpt_api, "chat", fake_chat)
    sess = await sessions.create_session(autonomy="advisor")
    # advisor видит только read-tools — эмулируем, что модель всё же
    # вызвала write (защита на исполнении, не только на схемах).
    res = await loop.run_agent_turn(sess.uuid, "запусти", max_iterations=3)
    # после denied модель получает error и (в тесте) снова зовёт тот же tool
    # → упираемся в лимит, но главное: denied в журнале.
    actions = await journal.recent_actions(session_id=sess.id)
    assert actions[0]["status"] == "denied"


@pytest.mark.asyncio
async def test_iteration_cap(env, monkeypatch) -> None:
    n = {"i": 0}

    async def fake_chat(**kwargs):
        n["i"] += 1
        return _fc_result("get_runtime_streams", {}, call_id=f"call_{n['i']}")

    monkeypatch.setattr(loop.gpt_api, "chat", fake_chat)
    sess = await sessions.create_session()
    res = await loop.run_agent_turn(sess.uuid, "следи вечно", max_iterations=3)
    assert "лимит" in res["text"]
    assert n["i"] == 3


@pytest.mark.asyncio
async def test_stop_flag_breaks_turn(env, monkeypatch) -> None:
    sess = await sessions.create_session()

    async def fake_chat(**kwargs):
        loop.stop_agent(sess.uuid)
        return _fc_result("get_runtime_streams", {})

    monkeypatch.setattr(loop.gpt_api, "chat", fake_chat)
    res = await loop.run_agent_turn(sess.uuid, "стоп", max_iterations=5)
    assert "Остановлено" in res["text"]


@pytest.mark.asyncio
async def test_read_tool_get_project(env) -> None:
    from app.services.agent.registry import execute_tool

    engine, factory = env
    async with factory() as s:
        s.add(
            Project(
                id=7, slug="seven", topic="cats",
                status=ProjectStatus.splitting, auto_mode=False,
                meta={"step_failure": {"step": "split", "error": "boom"}},
            )
        )
        s.add(
            Frame(
                project_id=7, number=1, uuid="f-uuid-7-1", voiceover_text="t",
                attrs={"video_gen_fail_count": 3},
            )
        )
        await s.commit()
    out = json.loads(await execute_tool("get_project", '{"project_id": 7}'))
    assert out["status"] == "splitting"
    assert out["step_failure"]["error"] == "boom"
    fails = json.loads(await execute_tool("get_frame_video_failures", '{"project_id": 7}'))
    assert fails["failures"][0]["fail_count"] == 3
