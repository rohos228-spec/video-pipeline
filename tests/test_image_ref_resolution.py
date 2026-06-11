"""Резолв референсов для шага «Картинки» из id в листе «план»."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.orchestrator.steps.generate_images import (
    _collect_ref_paths,
    _find_ref_file_any,
    _parse_ref_ids,
    normalize_ref_id,
    ref_id_file_aliases,
)


class _FakeProject:
    def __init__(self, data_dir: Path, project_id: int = 99) -> None:
        self.id = project_id
        self.data_dir = data_dir


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


@pytest.mark.asyncio
async def test_collect_ref_paths_two_characters(tmp_path: Path) -> None:
    data_dir = tmp_path / "proj"
    chars = data_dir / "characters"
    chars.mkdir(parents=True)
    c01 = chars / "c01.png"
    c02 = chars / "c02.png"
    c01.write_bytes(b"x" * 300_000)
    c02.write_bytes(b"x" * 300_000)
    project = _FakeProject(data_dir)

    refs = await _collect_ref_paths(
        None,
        project,
        ["c01", "c02"],
        kind="character",
        base_dir=chars,
        frame_number=1,
        max_count=2,
    )
    assert refs == [c01, c02]


@pytest.mark.asyncio
async def test_collect_ref_paths_char_then_item_slots(tmp_path: Path) -> None:
    data_dir = tmp_path / "proj"
    chars = data_dir / "characters"
    items = data_dir / "items"
    chars.mkdir(parents=True)
    items.mkdir(parents=True)
    c01 = chars / "c01.png"
    c02 = chars / "c02.png"
    i01 = items / "i01.png"
    for p in (c01, c02, i01):
        p.write_bytes(b"x" * 300_000)
    project = _FakeProject(data_dir)

    char_refs = await _collect_ref_paths(
        None,
        project,
        ["c01", "c02"],
        kind="character",
        base_dir=chars,
        frame_number=1,
        max_count=2,
    )
    assert char_refs == [c01, c02]

    item_refs = await _collect_ref_paths(
        None,
        project,
        ["i01"],
        kind="item",
        base_dir=items,
        frame_number=1,
        max_count=2 - len(char_refs),
    )
    assert item_refs == []
