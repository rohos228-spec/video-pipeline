"""Запись и воспроизведение LLM-фикстуры по согласованному ключу."""

from __future__ import annotations

import json
from pathlib import Path

from app.orchestrator.rag.llm_fixtures import RecordingRouter, ReplayRouter, ScriptedRouter, fixture_key
from app.orchestrator.rag.types import LLMResponse, LLMUsage


def _answer(tier, messages, stream=False, *, temperature=None, max_tokens=None, reasoning_effort=None):
    del stream, temperature, max_tokens, reasoning_effort
    return LLMResponse(
        text="ответ скрипта",
        call_id="script-1",
        model="gpt-5.6-sol",
        provider="scripted",
        tier=tier,
        finish_reason="stop",
        usage=LLMUsage(prompt_tokens=3, completion_tokens=4, reasoning_tokens=1),
        cost_usd=0.0,
        latency_ms=5,
        fallback_index=0,
        deterministic=True,
    )


def test_record_then_replay_same_key(tmp_path: Path) -> None:
    messages = [
        {"role": "user", "content": "привет"},
        {"extra": "ignored", "role": "system", "content": "sys"},
    ]
    # normalize оставит только role/content; для ключа передаём уже чистые сообщения в call.
    clean = [{"role": "system", "content": "sys"}, {"role": "user", "content": "привет"}]
    recorder = RecordingRouter(ScriptedRouter(_answer), tmp_path, clock=lambda: "2026-10-17T00:00:00Z")
    recorded = recorder.call(
        "strong",
        clean,
        False,
        temperature=0.2,
        max_tokens=16,
        reasoning_effort="high",
    )
    key = fixture_key("strong", clean, 0.2, 16, "high")
    assert recorded.fixture_key == key
    payload = json.loads((tmp_path / f"{key}.json").read_text(encoding="utf-8"))
    assert payload["key"] == key
    assert payload["recorded_at"] == "2026-10-17T00:00:00Z"
    assert payload["recorded_model"] == "gpt-5.6-sol"
    assert payload["request_meta"]["tier"] == "strong"
    assert payload["request_meta"]["reasoning_effort"] == "high"
    assert payload["request_meta"]["temperature"] == 0.2
    assert "messages" not in payload["request_meta"]
    assert payload["request_meta"]["messages_sha256"]
    assert payload["response"]["usage"]["reasoning_tokens"] == 1
    assert payload["response"]["latency_ms"] == 5
    assert payload["response"]["call_id"] == "script-1"
    assert set(payload) == {"key", "request_meta", "response", "recorded_at", "recorded_model"}

    replay = ReplayRouter(tmp_path)
    played = replay.call(
        "strong",
        clean,
        True,
        temperature=0.2,
        max_tokens=16,
        reasoning_effort="high",
    )
    assert played.text == "ответ скрипта"
    assert played.fixture_key == key
    assert played.model == "gpt-5.6-sol"
    assert played.call_id != "script-1"
    assert messages[0]["content"] == "привет"


def test_whole_number_temperature_shares_key() -> None:
    messages = [{"role": "user", "content": "один"}]
    assert fixture_key("strong", messages, 1, 16, "high") == fixture_key("strong", messages, 1.0, 16, "high")
    assert fixture_key("fast", messages, None, None, "") == fixture_key("fast", messages, None, None, None)
