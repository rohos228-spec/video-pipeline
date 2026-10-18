"""Неблокирующее: пустой effort в UI и на ноде — high."""

from app.services.vibecode_catalog import read_node_reasoning_effort


def test_missing_reasoning_effort_is_high() -> None:
    assert read_node_reasoning_effort({"data": {}}) == "high"
    assert read_node_reasoning_effort({"data": {"reasoningEffort": "low"}}) == "low"
    assert read_node_reasoning_effort({"data": {"reasoningEffort": "med"}}) == "medium"
    picker = open("web/src/components/canvas/node-model-picker.tsx", encoding="utf-8").read()
    assert 'if (!x) return "high";' in picker
    assert '{ id: "high", label: "high", note: "по умолчанию" }' in picker
