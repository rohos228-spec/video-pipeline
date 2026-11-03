"""Chat SSE deltas are incremental; message.content is a full snapshot."""

import json

import pytest

from app.services.gpt_api import GptApiError, parse_chat_completions_sse_lines


def _event(choice: dict) -> str:
    return "data: " + json.dumps({"choices": [choice]}, ensure_ascii=False)


def test_final_snapshot_does_not_duplicate_streamed_voiceover() -> None:
    vo = "[1] Начало.\n[2] Продолжение.\n"
    final = {"choices": [{"message": {"content": vo}, "finish_reason": "stop"}]}
    lines = [
        _event({"delta": {"content": "[1] Начало.\n"}}),
        _event({"delta": {"content": "[2] Продолжение.\n"}}),
        "data: " + json.dumps(final, ensure_ascii=False),
        "data: [DONE]",
    ]

    text, finish, raw = parse_chat_completions_sse_lines(lines)

    assert text == vo
    assert finish == "stop"
    assert raw == final


@pytest.mark.parametrize("snapshot", ["Исправлено", "x", "", "  ", []])
def test_snapshot_is_authoritative_not_longest_or_prefix_match(snapshot) -> None:
    lines = [
        _event({"delta": {"content": "Неполный или неверный длинный ответ"}}),
        _event({"message": {"content": snapshot}, "finish_reason": "length"}),
    ]

    text, finish, _ = parse_chat_completions_sse_lines(lines)

    assert text == ("" if snapshot == [] else snapshot)
    assert finish == "length"


def test_snapshot_only_uses_latest_full_message() -> None:
    lines = [
        _event({"message": {"content": "Начало"}}),
        _event({"message": {"content": "Начало конец"}}),
        _event({"message": {"content": "Начало конец"}, "finish_reason": "stop"}),
    ]

    assert parse_chat_completions_sse_lines(lines)[0] == "Начало конец"


def test_delta_only_preserves_intentionally_repeated_chunks() -> None:
    lines = [_event({"delta": {"content": "Эхо. "}})] * 2
    lines += [_event({"delta": {}, "finish_reason": "length"})]

    text, finish, _ = parse_chat_completions_sse_lines(lines)

    assert text == "Эхо. Эхо. "
    assert finish == "length"


def test_snapshot_preserves_intentionally_repeated_content() -> None:
    repeated = "Повтор. Повтор. "
    lines = [
        _event({"delta": {"content": "Повтор. "}}),
        _event({"delta": {"content": "Повтор. "}}),
        _event({"message": {"content": repeated}}),
    ]

    assert parse_chat_completions_sse_lines(lines)[0] == repeated


def test_snapshot_wins_when_delta_and_message_share_event() -> None:
    lines = [
        _event({"delta": {"content": "prefix "}}),
        _event({"delta": {"content": "tail"}, "message": {"content": "final"}}),
    ]

    assert parse_chat_completions_sse_lines(lines)[0] == "final"


def test_content_part_lists_and_usage_trailer() -> None:
    trailer = {"choices": [], "usage": {"completion_tokens": 5}}
    lines = [
        _event({"delta": {"content": [{"type": "text", "text": "partial"}]}}),
        _event({"message": {"content": [
            {"type": "text", "text": "Полный "},
            {"type": "text", "text": "ответ"},
        ]}, "finish_reason": "stop"}),
        "data: " + json.dumps(trailer),
    ]

    text, finish, raw = parse_chat_completions_sse_lines(lines)

    assert text == "Полный ответ"
    assert finish == "stop"
    assert raw == trailer


@pytest.mark.parametrize("message", [{}, {"content": None}, {"role": "assistant"}])
def test_message_without_text_does_not_erase_deltas(message) -> None:
    lines = [
        _event({"delta": {"content": "kept"}}),
        _event({"message": message, "finish_reason": "stop"}),
    ]

    assert parse_chat_completions_sse_lines(lines)[0] == "kept"


def test_error_after_snapshot_keeps_snapshot() -> None:
    lines = [
        _event({"delta": {"content": "partial"}}),
        _event({"message": {"content": "complete"}}),
        'data: {"error":{"message":"upstream error"}}',
    ]

    assert parse_chat_completions_sse_lines(lines)[0] == "complete"


def test_error_without_content_is_still_raised() -> None:
    with pytest.raises(GptApiError, match="upstream error"):
        parse_chat_completions_sse_lines([
            'data: {"error":{"message":"upstream error"}}',
        ])
