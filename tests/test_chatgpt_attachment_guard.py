"""ChatGPT attachment guard: failure phrases and health checks."""

from __future__ import annotations

from app.bots.chatgpt import (
    attachment_health_is_ok,
    attachment_name_visible_in_text,
    find_attachment_failure_phrases,
    format_attachment_health_error,
)


def test_find_attachment_failure_phrases_english() -> None:
    text = "project.xlsx\nUpload failed\nprompt.txt"
    found = find_attachment_failure_phrases(text)
    assert "upload failed" in found


def test_find_attachment_failure_phrases_russian() -> None:
    text = "Не удалось загрузить файл project.xlsx"
    found = find_attachment_failure_phrases(text)
    assert "не удалось загрузить" in found


def test_attachment_health_ok_when_complete() -> None:
    health = {
        "count": 2,
        "expected": 2,
        "loading": 0,
        "missing": [],
        "errors": [],
    }
    assert attachment_health_is_ok(health)


def test_attachment_health_fail_on_missing_name() -> None:
    health = {
        "count": 2,
        "expected": 2,
        "loading": 0,
        "missing": ["project.xlsx"],
        "errors": [],
    }
    assert not attachment_health_is_ok(health)
    assert "project.xlsx" in format_attachment_health_error(health)


def test_attachment_health_fail_on_error_phrase() -> None:
    health = {
        "count": 1,
        "expected": 2,
        "loading": 0,
        "missing": [],
        "errors": ["Upload failed"],
    }
    assert not attachment_health_is_ok(health)


def test_attachment_health_fail_while_loading() -> None:
    health = {
        "count": 2,
        "expected": 2,
        "loading": 1,
        "missing": [],
        "errors": [],
    }
    assert not attachment_health_is_ok(health)


def test_one_file_missing_other_visible() -> None:
    text = "prompt_script.txt\nUpload failed"
    assert attachment_name_visible_in_text("prompt_script.txt", text)
    assert not attachment_name_visible_in_text("project.xlsx", text)
