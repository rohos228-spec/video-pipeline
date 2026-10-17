"""Повторное ревью #352 №20: пустой промпт не режет KIE до проверки полей."""

from __future__ import annotations

from pathlib import Path


def test_empty_prompt_throw_is_after_kie_branch() -> None:
    text = Path("web/src/components/outsee/outsee-create-workspace.tsx").read_text(
        encoding="utf-8"
    )
    fn = text.split("const createGenerate = useMutation", 1)[1]
    head, tail = fn.split("if (kieActive && kieModel)", 1)
    assert 'if (!text) throw new Error("Введите промпт")' not in head
    assert "forceSingle && !text" in head
    assert 'if (!text) throw new Error("Введите промпт")' in tail
