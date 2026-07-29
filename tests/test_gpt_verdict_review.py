"""Tests for gpt_verdict_review parser."""

from app.services.gpt_verdict_review import parse_gpt_verdict


def test_parse_approved() -> None:
    r = parse_gpt_verdict("Вердикт: Одобрено (все хорошо)")
    assert r.approved is True


def test_parse_rejected() -> None:
    r = parse_gpt_verdict("Вердикт: Не одобрено: поправь хук")
    assert r.approved is False
    assert "хук" in r.fix_text


def test_parse_empty() -> None:
    r = parse_gpt_verdict("")
    assert r.approved is False


def test_parse_check_script_json_regen() -> None:
    raw = """{
  "decision": "regen",
  "confidence": 0.98,
  "criteria": {"flow_continuity": {"verdict": "pass"}},
  "fix_hints": ["убери мета-комментарии"],
  "issues": ["есть ИТОГО"]
}"""
    r = parse_gpt_verdict(raw)
    assert r.approved is False
    assert r.structured_check is True
    assert "мета" in r.fix_text


def test_parse_check_script_json_approved() -> None:
    raw = '{"decision": "approved", "confidence": 0.9, "criteria": {}}'
    r = parse_gpt_verdict(raw)
    assert r.approved is True
    assert r.structured_check is True
