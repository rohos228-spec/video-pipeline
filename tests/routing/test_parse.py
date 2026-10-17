"""Разбор OpenAI chat и kie responses без сети."""

from __future__ import annotations

from app.orchestrator.routing.transport import (
    ProviderRequest,
    build_body,
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


def test_parse_chat_sse_skips_broken_and_keepalive():
    raw = "\n".join(
        [
            ": keep-alive",
            "",
            "data: {not-json",
            'data: {"choices":[{"delta":{"content":"ок"},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]
    )
    result = parse_chat_sse(raw)
    assert result.text == "ок"
    assert result.finish_reason == "stop"


def test_parse_responses_sse_skips_broken_lines():
    raw = "\n".join(
        [
            ": ping",
            "data: {",
            'data: {"type":"response.output_text.delta","delta":"ок"}',
            'data: {"type":"response.completed","response":{"model":"gpt-5-6-sol","status":"completed","usage":{"input_tokens":1,"output_tokens":1,"output_tokens_details":{"reasoning_tokens":0}}}}',
        ]
    )
    result = parse_responses_sse(raw)
    assert result.text == "ок"
    assert result.model == "gpt-5-6-sol"


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


def test_chat_sse_usage_chunk_with_empty_choices():
    raw = "\n".join(
        [
            'data: {"choices":[{"delta":{"content":"ок"}}]}',
            'data: {"choices":[],"usage":{"prompt_tokens":11,"completion_tokens":2,"completion_tokens_details":{"reasoning_tokens":4}}}',
        ]
    )
    result = parse_chat_sse(raw)
    assert result.text == "ок"
    assert result.prompt_tokens == 11
    assert result.completion_tokens == 2
    assert result.reasoning_tokens == 4


def test_kie_responses_body_has_no_stream_options():
    request = ProviderRequest(
        model="gpt-5-6-sol",
        provider="kie",
        messages=[{"role": "user", "content": "да"}],
        temperature=None,
        max_tokens=None,
        reasoning_effort="high",
        timeout_s=30,
        stream=True,
    )
    body = build_body(request, "openai-responses")
    assert body["stream"] is True
    assert "stream_options" not in body
