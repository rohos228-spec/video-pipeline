"""Резолв референсов для шага «Картинки» из id в листе «план»."""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.steps.generate_images import (
    _find_ref_file_any,
    _parse_ref_ids,
    normalize_ref_id,
    ref_id_file_aliases,
)


def test_normalize_ref_id_strips_colon() -> None:
    assert normalize_ref_id("c02:") == "c02"
    assert normalize_ref_id("I01") == "i01"


def test_normalize_ref_id_rejects_garbage() -> None:
    assert normalize_ref_id("фридрих") is None
    assert normalize_ref_id("ницше") is None


def test_parse_ref_ids_filters_invalid_tokens() -> None:
    assert _parse_ref_ids("c02, фридрих, i01") == ["c02", "i01"]


def test_ref_id_aliases_i01_predmet1() -> None:
    assert "predmet1" in ref_id_file_aliases("i01")
    assert "i01" in ref_id_file_aliases("predmet1")


def test_find_ref_file_any_predmet_legacy(tmp_path: Path) -> None:
    items = tmp_path / "items"
    items.mkdir()
    legacy = items / "predmet1_abc123.png"
    legacy.write_bytes(b"x" * 300_000)
    found = _find_ref_file_any(items, "i01")
    assert found == legacy
