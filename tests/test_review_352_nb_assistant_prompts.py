"""Неблокирующее: заглушка стиля не режет ручной промпт, индексы draft не съезжают."""

from __future__ import annotations

import subprocess
from pathlib import Path


def test_assistant_prompt_pairing() -> None:
    workspace = Path("web/src/components/outsee/outsee-create-workspace.tsx").read_text(encoding="utf-8")
    assert "isStyleGuideStub" in workspace
    assert 'includes("not example objects from the style guide")' not in workspace
    assert "pairAssistantPrompts" in workspace
    subprocess.check_call(
        [
            "node",
            "--experimental-strip-types",
            "--test",
            "web/src/lib/assistant-prompts.test.ts",
        ]
    )
