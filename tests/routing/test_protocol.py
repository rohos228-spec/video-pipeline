"""Сигнатура call/classify совместима с ModelRouter из ws3-r3 (04a5506a)."""

from __future__ import annotations

import inspect

import pytest

from app.orchestrator.routing.model_router import LiveRouter, ModelRouter
from app.orchestrator.routing.types import (
    ApprovalEvent,
    ApprovalReason,
    LLMResponse,
    MessageMode,
    TaskStatus,
)
from tests.routing.fakes import FakeTransport, ok


def _params(fn):
    return [p for p in inspect.signature(fn).parameters.values() if p.name != "self"]


def test_call_and_classify_match_keyword_only_protocol():
    call = _params(ModelRouter.call)
    assert [p.name for p in call[:3]] == ["tier", "messages", "stream"]
    assert call[2].default is False
    assert all(p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for p in call[:3])
    by_name = {p.name: p for p in call}
    for name in ("run_id", "task_id", "agent_role", "budget_scope"):
        assert by_name[name].kind is inspect.Parameter.KEYWORD_ONLY
        assert by_name[name].default is None
    classify = _params(ModelRouter.classify)
    assert classify[0].name == "message"
    assert classify[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in classify[1:])


def test_response_fields_match_ws3_r3():
    assert [field.name for field in LLMResponse.__dataclass_fields__.values()][-2:] == [
        "task_status",
        "approval",
    ]
    assert LLMResponse.__annotations__["task_status"] == "TaskStatus | None"
    assert LLMResponse.__annotations__["approval"] == "ApprovalEvent | None"
    assert [field.name for field in ApprovalEvent.__dataclass_fields__.values()] == [
        "task_id",
        "action",
        "reason",
        "risk",
        "change_id",
        "payload",
    ]
    assert MessageMode.task == "task"
    assert TaskStatus.waiting_approval == "waiting_approval"
    assert ApprovalReason.budget_exhausted == "budget_exhausted"


async def test_supervisor_call_shape(config):
    transport = FakeTransport([ok("ok"), ok("next")])
    router = LiveRouter(config, transport=transport)
    messages = [{"role": "user", "content": "да"}]
    response = await router.call(
        "strong",
        messages,
        False,
        run_id="run-1",
        task_id="7",
        agent_role="supervisor",
        budget_scope="internal",
    )
    assert response.text == "ok"
    assert response.task_status is None
    assert response.approval is None
    assert transport.calls[0].provider == "52pay"

    omitted = await router.call("strong", messages, budget_scope=None, run_id="run-2")
    assert omitted.text == "next"

    decision = await router.classify("да")
    assert decision.mode == MessageMode.task
    assert decision.kill_switch is False

    stopped = await router.classify("аварийный стоп")
    assert stopped.kill_switch is True
    assert stopped.mode == MessageMode.interrupt

    with pytest.raises(TypeError):
        await router.call("strong", messages, False, "run-1")
