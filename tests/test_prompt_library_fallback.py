import pytest
from app.services.prompt_library import (
    DEFAULT_NAME,
    prompt_path,
    read_prompt,
    resolve_project_prompt_with_source,
)

def test_hero_default_prompt_exists():
    p = prompt_path("hero", DEFAULT_NAME)
    assert p.is_file(), f"File {p} must exist on disk"
    text = read_prompt("hero", DEFAULT_NAME)
    assert len(text) > 100
    assert "character model sheet" in text.lower() or "turnaround sheet" in text.lower()

def test_hero_prompt_resolve_fallback(monkeypatch, tmp_path):
    import app.services.prompt_library as pl
    hero_dir = tmp_path / "04_hero"
    hero_dir.mkdir(parents=True)
    custom = hero_dir / "custom_model_sheet.md"
    custom.write_text("Custom prompt content here...", encoding="utf-8")
    monkeypatch.setattr(pl, "PROMPTS_ROOT", tmp_path)
    name, source = resolve_project_prompt_with_source({}, "hero")
    assert name == "custom_model_sheet"
    assert source == "fallback"
    content = read_prompt("hero", DEFAULT_NAME)
    assert content == "Custom prompt content here..."
