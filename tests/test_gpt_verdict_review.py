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
