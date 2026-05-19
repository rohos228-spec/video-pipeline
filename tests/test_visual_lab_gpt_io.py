"""Tests for ``extract_json_object`` — the JSON unwrapper for ChatGPT replies."""

from __future__ import annotations

import pytest

from app.services.visual_lab.gpt_io import extract_json_object


def test_plain_json_object() -> None:
    out = extract_json_object('{"a": 1, "b": "two"}')
    assert out == {"a": 1, "b": "two"}


def test_fenced_json_block() -> None:
    text = (
        "Sure, here is the analysis:\n"
        "```json\n"
        '{"scores": {"color_harmony": 7}, "visual_pros": ["nice light"]}\n'
        "```\n"
        "Let me know if you need more detail!"
    )
    out = extract_json_object(text)
    assert out["scores"] == {"color_harmony": 7}


def test_unfenced_json_with_prose_around() -> None:
    text = (
        "Ниже результат оценки.\n\n"
        '{"scores": {"fur_quality": 6}, "extra": null}\n\n'
        "Готово!"
    )
    out = extract_json_object(text)
    assert out["scores"]["fur_quality"] == 6
    assert out["extra"] is None


def test_picks_largest_valid_object() -> None:
    text = (
        '{"tiny": true}\n'
        "and now the real one:\n"
        '{"real": true, "scores": {"fur_quality": 9}, "extra": [1,2,3,4,5]}\n'
    )
    out = extract_json_object(text)
    assert out.get("real") is True


def test_no_json_raises() -> None:
    with pytest.raises(ValueError):
        extract_json_object("Just prose, no JSON at all.")


def test_empty_raises() -> None:
    with pytest.raises(ValueError):
        extract_json_object("")


def test_nested_braces_in_strings_ignored() -> None:
    text = '{"label": "I have a {curly} brace inside", "n": 1}'
    out = extract_json_object(text)
    assert out["label"] == "I have a {curly} brace inside"
    assert out["n"] == 1
