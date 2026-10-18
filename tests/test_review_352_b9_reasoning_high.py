"""П.9: reasoning по умолчанию high."""

from __future__ import annotations

from pathlib import Path

from app.services.vibecode_catalog import read_node_reasoning_effort


def test_backend_reasoning_default_is_high() -> None:
    assert read_node_reasoning_effort(None) == "high"
    assert read_node_reasoning_effort({"data": {}}) == "high"
    assert read_node_reasoning_effort({"data": {"reasoningEffort": "low"}}) == "low"


def test_ui_reasoning_default_is_high() -> None:
    text = Path("web/src/components/canvas/node-model-picker.tsx").read_text(encoding="utf-8")
    assert '{ id: "high", label: "high", note: "по умолчанию" }' in text
    start = text.index("export function normalizeEffort")
    fn = text[start : text.index("function vendorNote")]
    assert 'return "high"' in fn
    assert fn.rstrip().endswith('return "high";\n}') or 'return "high";' in fn[fn.rfind("ultra") :]
