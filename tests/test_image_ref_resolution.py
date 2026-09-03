"""Резолв референсов для шага «Картинки» из id в листе «план»."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.orchestrator.steps.generate_images import (
    _collect_ref_paths,
    _find_ref_file_any,
    _load_refs_for_frame,
    _parse_ref_ids,
    _person_ids_from_frame,
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


class _FakeFrame:
    def __init__(self, attrs: dict) -> None:
        self.attrs = attrs


def test_person_ids_from_frame_reads_db_attrs() -> None:
    fr = _FakeFrame({"characters": "c02,c03", "persons": "c01"})
    assert _person_ids_from_frame(fr) == ["c02", "c03"]


def test_person_ids_from_frame_empty_when_no_ids() -> None:
    assert _person_ids_from_frame(_FakeFrame({})) == []
    assert _person_ids_from_frame(_FakeFrame({"characters": "ворота, лужи"})) == []


def test_ref_one_person_lock_prepends_when_missing() -> None:
    from app.orchestrator.steps.generate_images import (
        _REF_ONE_PERSON_LOCK,
        _with_ref_one_person_lock,
    )

    locked = _with_ref_one_person_lock("сцена")
    assert locked.startswith(_REF_ONE_PERSON_LOCK)
    assert locked.endswith("сцена")
    assert _with_ref_one_person_lock(locked) == locked


def test_child_frame_does_not_attach_character_sheets() -> None:
    from app.orchestrator.steps.generate_images import (
        _character_sheet_ids_for_image,
    )

    child = _FakeFrame(
        {
            "characters": "c02,c03",
            "camera_subdivide": {"role": "shot", "parent_uuid": "x"},
        }
    )
    parent = _FakeFrame(
        {
            "characters": "c02,c03",
            "camera_subdivide": {"role": "vo_parent"},
        }
    )
    assert _character_sheet_ids_for_image(child) == []
    assert _character_sheet_ids_for_image(parent) == ["c02", "c03"]


def test_ref_id_aliases_i01_predmet1() -> None:
    assert "predmet1" in ref_id_file_aliases("i01")
    assert "i01" in ref_id_file_aliases("predmet1")


def test_ref_id_aliases_cyrillic_c() -> None:
    assert "с02" in ref_id_file_aliases("c02")


def test_find_ref_file_any_cyrillic_c02(tmp_path: Path) -> None:
    chars = tmp_path / "characters"
    chars.mkdir()
    bad = chars / "с02.png"
    bad.write_bytes(b"x" * 300_000)
    found = _find_ref_file_any(chars, "c02")
    assert found == bad


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


@pytest.mark.asyncio
async def test_load_refs_uses_override_not_empty_xlsx(tmp_path: Path) -> None:
    """shot1 должен брать id из БД/override, даже если Excel пустой."""
    data_dir = tmp_path / "proj"
    chars = data_dir / "characters"
    chars.mkdir(parents=True)
    c02 = chars / "c02.png"
    c03 = chars / "c03.png"
    c02.write_bytes(b"x" * 300_000)
    c03.write_bytes(b"x" * 300_000)
    project = _FakeProject(data_dir)

    empty = await _load_refs_for_frame(None, project, 1)
    assert empty == []

    refs = await _load_refs_for_frame(
        None, project, 1, persons_override=["c02", "c03"]
    )
    assert refs == [c02, c03]
