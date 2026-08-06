"""gpt_api: нативный tool calling (function calling) для обоих API-режимов."""

from __future__ import annotations

import json

import httpx
import pytest

from app.services import gpt_api
from app.services.gpt_api import (
    chat,
    chat_tool_followup_messages,
    responses_tool_followup_input,
)

ADD_TOOL = {
    "type": "function",
    "name": "add_numbers",
    "description": "Add two integers.",
    "parameters": {
        "type": "object",
        "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
        "required": ["a", "b"],
    },
}


def _enable(monkeypatch, *, mode: str, path: str) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "text_llm_provider", "kie")
    monkeypatch.setattr(settings, "tokenrouter_api_key", "")
    monkeypatch.setattr(settings, "gpt_api_key", "test-key")
    monkeypatch.setattr(settings, "gpt_base_url", "https://gw.test")
    monkeypatch.setattr(settings, "gpt_chat_path", path)
    monkeypatch.setattr(settings, "gpt_api_mode", mode)
    monkeypatch.setattr(settings, "gpt_max_retries", 0)


def _mock_httpx(monkeypatch, handler) -> None:
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(gpt_api.httpx, "AsyncClient", factory)

    async def _no_sleep(*_a, **_k):
        return None

    monkeypatch.setattr(gpt_api.asyncio, "sleep", _no_sleep)


# ─────────────────────────── Responses API ───────────────────────────


def _responses_function_call_payload() -> dict:
    return {
        "status": "completed",
        "output": [
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_abc",
                "name": "add_numbers",
                "arguments": '{"a":17,"b":25}',
            }
        ],
        "usage": {"total_tokens": 5},
    }


@pytest.mark.asyncio
async def test_responses_tools_sent_and_parsed(monkeypatch) -> None:
    """Responses: tools/tool_choice уходят в body, function_call парсится,
    пустой текст при tool call — не ошибка."""
    _enable(monkeypatch, mode="responses", path="/codex/v1/responses")
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_responses_function_call_payload())

    _mock_httpx(monkeypatch, handler)
    res = await chat(
        prompt="сложи 17 и 25 через инструмент",
        tools=[ADD_TOOL],
        tool_choice="auto",
    )
    assert seen[0]["tools"] == [ADD_TOOL]
    assert seen[0]["tool_choice"] == "auto"
    assert res.text == ""
    assert len(res.tool_calls) == 1
    tc = res.tool_calls[0]
    assert tc["name"] == "add_numbers"
    assert tc["call_id"] == "call_abc"
    assert json.loads(tc["arguments"]) == {"a": 17, "b": 25}


@pytest.mark.asyncio
async def test_responses_no_tools_no_body_keys(monkeypatch) -> None:
    """Без tools — body как раньше (никаких tools/tool_choice)."""
    _enable(monkeypatch, mode="responses", path="/codex/v1/responses")
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "ok"}],
                    }
                ],
            },
        )

    _mock_httpx(monkeypatch, handler)
    res = await chat(prompt="hi")
    assert res.text == "ok"
    assert res.tool_calls == []
    assert "tools" not in seen[0]
    assert "tool_choice" not in seen[0]


def test_responses_followup_input_echo_and_output() -> None:
    original = [{"role": "user", "content": "сложи"}]
    payload = _responses_function_call_payload()
    follow = responses_tool_followup_input(original, payload, {"call_abc": '{"sum":42}'})
    assert follow[0] == original[0]
    fc = follow[1]
    assert fc["type"] == "function_call"
    out = follow[2]
    assert out == {
        "type": "function_call_output",
        "call_id": "call_abc",
        "output": '{"sum":42}',
    }


@pytest.mark.asyncio
async def test_responses_input_items_override(monkeypatch) -> None:
    """input_items подменяет build_input (follow-up шаг agent loop)."""
    _enable(monkeypatch, mode="responses", path="/codex/v1/responses")
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "42"}],
                    }
                ],
            },
        )

    _mock_httpx(monkeypatch, handler)
    items = [
        {"role": "user", "content": "сложи"},
        {
            "type": "function_call_output",
            "call_id": "call_abc",
            "output": '{"sum":42}',
        },
    ]
    res = await chat(prompt="игнор", input_items=items, tools=[ADD_TOOL])
    assert seen[0]["input"] == items
    assert res.text == "42"


# ─────────────────────────── chat/completions ───────────────────────────


def _chat_tool_calls_payload() -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_xyz",
                            "type": "function",
                            "function": {
                                "name": "add_numbers",
                                "arguments": '{"a":1,"b":2}',
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


@pytest.mark.asyncio
async def test_chat_mode_tool_calls_parsed(monkeypatch) -> None:
    """chat/completions: tool_calls из message парсятся, пустой content ок."""
    _enable(monkeypatch, mode="chat", path="/v1/chat/completions")
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_chat_tool_calls_payload())

    _mock_httpx(monkeypatch, handler)
    res = await chat(prompt="сложи", tools=[ADD_TOOL])
    assert seen[0]["tools"] == [ADD_TOOL]
    assert res.text == ""
    assert res.finish_reason == "tool_calls"
    assert res.tool_calls[0]["name"] == "add_numbers"
    assert res.tool_calls[0]["call_id"] == "call_xyz"


def test_chat_followup_messages() -> None:
    msgs = [{"role": "user", "content": "сложи"}]
    follow = chat_tool_followup_messages(msgs, _chat_tool_calls_payload(), {"call_xyz": "3"})
    assert follow[1]["role"] == "assistant"
    assert follow[1]["tool_calls"]
    assert follow[2] == {"role": "tool", "tool_call_id": "call_xyz", "content": "3"}


@pytest.mark.asyncio
async def test_chat_mode_messages_override(monkeypatch) -> None:
    _enable(monkeypatch, mode="chat", path="/v1/chat/completions")
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "done"}, "finish_reason": "stop"}]}
        )

    _mock_httpx(monkeypatch, handler)
    msgs = [{"role": "user", "content": "x"}]
    res = await chat(prompt="игнор", messages=msgs)
    assert seen[0]["messages"] == msgs
    assert res.text == "done"
