"""Логика мульти-рефов outsee (без браузера)."""

from __future__ import annotations

from pathlib import Path


def _slot_indices(file_input_count: int, ref_count: int) -> list[int]:
    """Зеркало _attach_reference_images_robust: какой input под какой ref."""
    return [i if file_input_count > i else file_input_count - 1 for i in range(ref_count)]


def test_two_refs_two_inputs_use_separate_slots() -> None:
    assert _slot_indices(2, 2) == [0, 1]


def test_two_refs_one_input_multi_file_path() -> None:
    # При count==1 вызывается multi-file; fallback слоты оба в 0.
    assert _slot_indices(1, 2) == [0, 0]
