"""№29: файловые промты ноды не прячутся за startsWith('')."""

from __future__ import annotations

from pathlib import Path


def test_prompt_files_panel_is_not_gated_by_empty_prefix() -> None:
    text = Path("web/src/components/studio/node-studio.tsx").read_text(encoding="utf-8")
    assert 'startsWith("")' not in text
    assert "У ноды нет файловых промтов." not in text
    assert "showFilesPanel && promptStepCode && !canCatalog" in text
    assert "<PromptFilesPanel" in text
