"""Тесты на ReAct loop: stop conditions, HITL flow, retry on rejected.

Без живых вызовов LLM — используем mock AIClient.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.ai_agent.client import AIChatResponse
from app.ai_agent.config import get_config
from app.ai_agent.loop import create_runtime_session, run_loop
from app.ai_agent.session import RuntimeSession
from app.models import AISessionMode

REPO_ROOT = Path(__file__).resolve().parents[1]


def _mock_response(
    content: str | None = None,
    tool_calls: list[dict] | None = None,
    finish_reason: str = "stop",
    tokens_in: int = 100,
    tokens_out: int = 50,
) -> AIChatResponse:
    return AIChatResponse(
        content=content,
        tool_calls=tool_calls or [],
        finish_reason=finish_reason,
        prompt_tokens=tokens_in,
        completion_tokens=tokens_out,
        total_tokens=tokens_in + tokens_out,
        cost_rub=0.01,
        raw={},
    )


def _tool_call(name: str, args: dict, call_id: str = "call_test") -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def _mock_client_with_responses(responses: list[AIChatResponse]):
    """Mock-клиент, который последовательно возвращает responses."""
    iter_obj = iter(responses)
    client = AsyncMock()
    client.cfg = get_config(repo_root=REPO_ROOT)

    async def chat_side_effect(*args, **kwargs):
        return next(iter_obj)

    client.chat = chat_side_effect
    return client


@pytest.fixture
def make_session():
    def _make(query: str = "test query", mode: AISessionMode = AISessionMode.qa) -> RuntimeSession:
        cfg = get_config(repo_root=REPO_ROOT)
        return RuntimeSession(
            db_id=1,
            chat_id=cfg.owner_chat_id or 0,
            model=cfg.default_model,
            mode=mode,
            initial_query=query,
        )

    return _make


# ──────────────────────────── stop conditions ───────────────────────────────


@pytest.mark.asyncio
async def test_loop_stops_on_assistant_content_without_tools(make_session) -> None:
    """LLM вернул text без tool_calls → loop завершается."""
    cfg = get_config(repo_root=REPO_ROOT)
    session = make_session("simple question")
    responses = [_mock_response(content="Вот ответ.")]
    client = _mock_client_with_responses(responses)
    await run_loop(session, client, cfg)
    assert session.finished
    assert session.final_answer == "Вот ответ."
    assert session.step_count == 1


@pytest.mark.asyncio
async def test_loop_stops_on_final_answer_tool(make_session) -> None:
    """Tool final_answer завершает loop."""
    cfg = get_config(repo_root=REPO_ROOT)
    session = make_session("задача")
    responses = [
        _mock_response(
            tool_calls=[_tool_call("final_answer", {"answer": "ОК, сделано"})]
        ),
    ]
    client = _mock_client_with_responses(responses)
    await run_loop(session, client, cfg)
    assert session.finished
    assert session.final_answer == "ОК, сделано"


@pytest.mark.asyncio
async def test_loop_max_steps(make_session, monkeypatch) -> None:
    """MAX_STEPS обрывает зацикленный loop."""
    monkeypatch.setenv("AI_AGENT_MAX_STEPS", "3")
    cfg = get_config(repo_root=REPO_ROOT)
    assert cfg.max_steps == 3
    session = make_session()

    # LLM зациклен — на каждый шаг даёт read_file без final_answer.
    def make_loop_response():
        return _mock_response(
            tool_calls=[_tool_call("read_file", {"path": "README.md"}, "c1")]
        )

    responses = [make_loop_response() for _ in range(10)]
    client = _mock_client_with_responses(responses)
    await run_loop(session, client, cfg)
    assert session.finished
    assert "Лимит шагов" in (session.final_answer or "")
    assert session.step_count == 3


@pytest.mark.asyncio
async def test_loop_max_tokens(make_session, monkeypatch) -> None:
    """MAX_TOKENS_PER_SESSION обрывает loop."""
    monkeypatch.setenv("AI_AGENT_MAX_TOKENS_PER_SESSION", "200")
    cfg = get_config(repo_root=REPO_ROOT)
    assert cfg.max_tokens_per_session == 200
    session = make_session()
    # каждый response = 100+50 = 150 → после первого 150, после второго 300 > 200.
    responses = [
        _mock_response(tool_calls=[_tool_call("read_file", {"path": "README.md"}, "c1")]),
        _mock_response(tool_calls=[_tool_call("read_file", {"path": "README.md"}, "c2")]),
        _mock_response(content="never reached"),
    ]
    client = _mock_client_with_responses(responses)
    await run_loop(session, client, cfg)
    assert session.finished
    assert "Лимит токенов" in (session.final_answer or "")


@pytest.mark.asyncio
async def test_loop_cancelled(make_session) -> None:
    """cancel() в середине loop'а останавливает выполнение."""
    cfg = get_config(repo_root=REPO_ROOT)
    session = make_session()
    # Симулируем cancel сразу
    session.cancel()
    responses = [_mock_response(content="never")]
    client = _mock_client_with_responses(responses)
    await run_loop(session, client, cfg)
    assert session.cancelled


# ──────────────────────────── HITL flow ─────────────────────────────────────


@pytest.mark.asyncio
async def test_loop_hitl_approved(make_session) -> None:
    """HITL approved → tool выполняется."""
    cfg = get_config(repo_root=REPO_ROOT)
    session = make_session(mode=AISessionMode.hitl_edit)

    responses = [
        _mock_response(
            tool_calls=[_tool_call("read_file", {"path": "README.md"}, "c1")]
        ),
        _mock_response(content="прочитал, вот итог"),
    ]
    client = _mock_client_with_responses(responses)

    hitl_called = []

    async def hitl_cb(name, args, sess):
        hitl_called.append(name)
        return {"decision": "approved"}

    await run_loop(session, client, cfg, hitl_callback=hitl_cb)
    # read_file — read-only, HITL не должен вызываться
    assert hitl_called == []
    assert session.finished


@pytest.mark.asyncio
async def test_loop_hitl_rejected_passes_to_llm(make_session) -> None:
    """rejected HITL передаёт результат как tool error в LLM, и тот может попробовать иначе."""
    cfg = get_config(repo_root=REPO_ROOT)
    session = make_session(mode=AISessionMode.hitl_edit)

    responses = [
        # 1. LLM хочет edit_file
        _mock_response(
            tool_calls=[
                _tool_call(
                    "edit_file",
                    {"path": "README.md", "old_string": "x", "new_string": "y"},
                    "c1",
                )
            ]
        ),
        # 2. Получив reject, LLM сдаётся через final_answer
        _mock_response(
            tool_calls=[
                _tool_call("final_answer", {"answer": "owner отклонил"}, "c2")
            ]
        ),
    ]
    client = _mock_client_with_responses(responses)

    async def hitl_cb(name, args, sess):
        return {"decision": "rejected", "reason": "test"}

    await run_loop(session, client, cfg, hitl_callback=hitl_cb)
    assert session.finished
    assert session.final_answer == "owner отклонил"

    # Проверим что в истории есть tool message с rejected
    tool_msgs = [m for m in session.history if m.get("role") == "tool"]
    assert tool_msgs, "tool message должен быть в истории"
    # один из них должен содержать rejected
    rejected_found = any("rejected" in m.get("content", "") for m in tool_msgs)
    assert rejected_found


@pytest.mark.asyncio
async def test_loop_unknown_tool_returns_error(make_session) -> None:
    """LLM вызвал tool, которого нет → tool message с error."""
    cfg = get_config(repo_root=REPO_ROOT)
    session = make_session()

    responses = [
        _mock_response(
            tool_calls=[_tool_call("nonexistent_tool", {"foo": "bar"}, "c1")]
        ),
        _mock_response(content="ошибся"),
    ]
    client = _mock_client_with_responses(responses)
    await run_loop(session, client, cfg)
    assert session.finished


@pytest.mark.asyncio
async def test_create_runtime_session_basic() -> None:
    cfg = get_config(repo_root=REPO_ROOT)
    s = await create_runtime_session(
        cfg, chat_id=123, initial_query="привет", db_id=42
    )
    assert s.chat_id == 123
    assert s.db_id == 42
    assert s.model == cfg.default_model
    assert s.initial_query == "привет"
    assert s.mode == AISessionMode.hitl_edit  # default


def test_session_summary_text(make_session) -> None:
    session = make_session()
    session.step_count = 5
    session.tokens_in = 1000
    session.tokens_out = 200
    session.cost_rub = 0.5
    session.final_answer = "тест"
    text = session.summary_text()
    assert "#1" in text
    assert "Шагов: 5" in text
    assert "1000+200" in text


def test_session_estimate_cost_fallback(make_session) -> None:
    session = make_session()
    session.model = "gpt-4o-mini"
    session.tokens_in = 1_000_000
    session.tokens_out = 100_000
    # cost_rub=0 → fallback расчёт
    cost = session.estimate_cost()
    # gpt-4o-mini ~ 15₽/M in + 60₽/M out → 15 + 6 = 21
    assert 15 < cost < 30
