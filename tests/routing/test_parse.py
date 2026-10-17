"""Разбор OpenAI chat и kie responses без сети."""

from __future__ import annotations

from app.orchestrator.routing.transport import (
    parse_chat_payload,
    parse_chat_sse,
    parse_responses_payload,
    parse_responses_sse,
)


def test_parse_chat_payload_reads_usage():
    result = parse_chat_payload(
        {
            "model": "glm-5.3-flash",
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": "привет"},
                }
            ],
            "usage": {
                "prompt_tokens": 5,
                "completion_tokens": 2,
                "completion_tokens_details": {"reasoning_tokens": 0},
            },
        }
    )
    assert result.text == "привет"
    assert result.model == "glm-5.3-flash"
    assert result.prompt_tokens == 5
    assert result.completion_tokens == 2
    assert result.reasoning_tokens == 0


def test_parse_responses_payload():
    result = parse_responses_payload(
        {
            "model": "gpt-5.6-sol",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "план"}],
                }
            ],
            "usage": {
                "input_tokens": 8,
                "output_tokens": 3,
                "output_tokens_details": {"reasoning_tokens": 4},
            },
        }
    )
    assert result.text == "план"
    assert result.prompt_tokens == 8
    assert result.completion_tokens == 3
    assert result.reasoning_tokens == 4


def test_parse_chat_sse_concatenates_deltas():
    raw = "\n".join(
        [
            'data: {"model":"gpt-5.6-sol","choices":[{"delta":{"content":"ча"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"content":"сть"},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]
    )
    result = parse_chat_sse(raw)
    assert result.text == "часть"
    assert result.finish_reason == "stop"
    assert result.model == "gpt-5.6-sol"


def test_parse_responses_sse_reads_deltas():
    raw = "\n".join(
        [
            'data: {"type":"response.output_text.delta","delta":"ки"}',
            'data: {"type":"response.output_text.delta","delta":"е"}',
            'data: {"type":"response.completed","response":{"model":"gpt-5-6-sol","status":"completed","usage":{"input_tokens":2,"output_tokens":1,"output_tokens_details":{"reasoning_tokens":3}}}}',
        ]
    )
    result = parse_responses_sse(raw)
    assert result.text == "кие"
    assert result.model == "gpt-5-6-sol"
    assert result.prompt_tokens == 2
    assert result.reasoning_tokens == 3
