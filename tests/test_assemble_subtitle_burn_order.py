"""Регрессия: montage-v3 должен писать ASS до проверки файла, не пропускать burn."""

from __future__ import annotations

from pathlib import Path


def test_montage_subtitle_burn_does_not_gate_on_missing_ass() -> None:
    src = Path("app/orchestrator/steps/assemble.py").read_text(encoding="utf-8")
    # Исторический баг: gate на is_file() до make_simple_ass — burn на montage-v3
    # никогда не запускался (ASS ещё не записан).
    assert "subs_path is not None and subs_path.is_file()" not in src
    assert "make_simple_ass(sub_entries, subs_path" in src
