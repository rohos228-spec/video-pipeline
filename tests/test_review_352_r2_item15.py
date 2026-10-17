"""Повторное ревью #352 №15: разбор стиля идёт через бюджет LLM."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_analyze_style_images_uses_llm_budget(tmp_path: Path, monkeypatch) -> None:
    image = tmp_path / "ref.png"
    image.write_bytes(b"png")
    calls: list[str] = []

    async def fake_ask(text, *, files=None, system=None, budget=None, label="", **_k):
        assert budget is not None
        calls.append(label)
        if label == "style_synth":
            return '{"name":"Noir","desc":"d","category":"c","prompt_core":"ink"}'
        return '{"palette":"red"}'

    monkeypatch.setattr("app.services.style_agent.llm.ask", fake_ask)
    from app.services.style_analyzer import analyze_style_images

    entry = await analyze_style_images([image])
    assert entry["prompt_core"] == "ink"
    assert calls == ["style_notes", "style_synth"]
    text = Path("app/services/style_analyzer.py").read_text(encoding="utf-8")
    assert "get_gpt_client" not in text
    assert "ask_with_files" not in text
