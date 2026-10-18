"""Неблокирующее ревью #352: скобки внутри строки не считаются обрывом JSON."""

from __future__ import annotations


def test_brackets_inside_string_are_not_truncated() -> None:
    from app.services.scene_grammar_batches import diagnose_apply_ops_text

    text = '{"ops":[{"fields":{"закадр":"герой [идёт] и {молчит}"}}]}'
    reason, _data = diagnose_apply_ops_text(text)
    assert reason != "json_truncated"


def test_unclosed_object_is_truncated() -> None:
    from app.services.scene_grammar_batches import diagnose_apply_ops_text

    reason, data = diagnose_apply_ops_text('{"ops":[{"fields":{"закадр":"кусок"')
    assert reason == "json_truncated"
    assert data is None
