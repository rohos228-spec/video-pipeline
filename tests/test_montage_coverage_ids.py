"""Живые номера кадров vs stale shot_id / leftover parent_id / дубль VO-хвоста."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from app.models import FrameStatus
from app.services.scan_frames import frame_needs_shot1_image
from app.services.vo_shot_expand import (
    coverage_shot_id,
    detach_duplicate_vo_replica_cells,
    find_coverage_parent_frame,
    is_coverage_leftover,
    is_shot_child,
    sync_live_coverage_ids,
)


def _fr(
    number: int,
    *,
    uuid: str,
    role: str,
    shot_id: str,
    parent_uuid: str = "",
    coverage_parent_id: str = "",
    vo: str = "текст",
    leftover: bool = False,
    coverage_kind: str = "",
    kadry: list[dict] | None = None,
    shot_index: int | None = None,
) -> SimpleNamespace:
    cs: dict = {
        "role": role,
        "shot_id": shot_id,
        "parent_uuid": parent_uuid or uuid,
        "coverage_parent_id": coverage_parent_id,
        "leftover": leftover,
    }
    if coverage_kind:
        cs["coverage_kind"] = coverage_kind
    if shot_index is not None:
        cs["shot_index"] = shot_index
    attrs: dict = {"camera_subdivide": cs}
    if kadry is not None:
        attrs["кадры"] = kadry
    return SimpleNamespace(
        number=number,
        uuid=uuid,
        voiceover_text=vo,
        image_prompt="p",
        status=FrameStatus.image_prompt_ready,
        attrs=attrs,
    )


def test_sync_rewrites_stale_shot_id_to_live_parent_number() -> None:
    pu = "p" * 24
    parent = _fr(
        13,
        uuid=pu,
        role="vo_parent",
        shot_id="12-S3-K1",
        coverage_parent_id="1-S1-K1",
        shot_index=1,
        kadry=[
            {"id": "12-S3-K1", "parent_id": None},
            {"id": "12-S3-K2", "parent_id": "12-S3-K1"},
        ],
    )
    child = _fr(
        14,
        uuid="c" * 24,
        role="shot",
        shot_id="12-S3-K2",
        parent_uuid=pu,
        coverage_parent_id="12-S3-K1",
        shot_index=2,
        kadry=[{"id": "12-S3-K2", "parent_id": "12-S3-K1"}],
    )
    n = sync_live_coverage_ids([parent, child])
    assert n >= 2
    assert coverage_shot_id(parent) == "13-S3-K1"
    assert coverage_shot_id(child) == "13-S3-K2"
    assert parent.attrs["camera_subdivide"]["coverage_parent_id"] == ""
    assert child.attrs["camera_subdivide"]["coverage_parent_id"] == "13-S3-K1"
    assert parent.attrs["кадры"][0]["id"] == "13-S3-K1"
    assert parent.attrs["кадры"][1]["id"] == "13-S3-K2"
    assert child.attrs["кадры"][0]["parent_id"] == "13-S3-K1"


def test_vo_parent_leftover_id_is_not_still_parent() -> None:
    k1 = _fr(1, uuid="a" * 24, role="vo_parent", shot_id="1-S1-K1")
    later = _fr(
        13,
        uuid="b" * 24,
        role="vo_parent",
        shot_id="12-S3-K1",
        coverage_parent_id="1-S1-K1",
    )
    assert find_coverage_parent_frame([k1, later], later) is None


def test_shot_child_still_uses_parent_uuid() -> None:
    pu = "p" * 24
    parent = _fr(13, uuid=pu, role="vo_parent", shot_id="12-S3-K1")
    child = _fr(
        14,
        uuid="c" * 24,
        role="shot",
        shot_id="12-S3-K2",
        parent_uuid=pu,
        coverage_parent_id="1-S1-K1",
        shot_index=2,
    )
    assert find_coverage_parent_frame([parent, child], child) is parent


def test_detach_replica_cell_marks_leftover_and_unlinks() -> None:
    p1 = "1" * 24
    p2 = "2" * 24
    a = _fr(1, uuid=p1, role="vo_parent", shot_id="1-S1-K1", vo="открытие ролика", shot_index=1)
    b = _fr(
        2,
        uuid="x" * 24,
        role="shot",
        shot_id="1-S1-K2",
        parent_uuid=p1,
        vo="второй кусок",
        shot_index=2,
    )
    dup_p = _fr(
        10,
        uuid=p2,
        role="vo_parent",
        shot_id="9-S9-K1",
        vo="открытие ролика",
        shot_index=1,
    )
    dup_c1 = _fr(
        11,
        uuid="y" * 24,
        role="shot",
        shot_id="9-S9-K2",
        parent_uuid=p2,
        vo="второй кусок",
        shot_index=2,
    )
    dup_c2 = _fr(
        12,
        uuid="z" * 24,
        role="shot",
        shot_id="9-S9-K3",
        parent_uuid=p2,
        vo="второй кусок",
        shot_index=3,
    )
    n = detach_duplicate_vo_replica_cells([a, b, dup_p, dup_c1, dup_c2])
    assert n == 3
    assert is_coverage_leftover(dup_p)
    assert is_coverage_leftover(dup_c1)
    assert not is_shot_child(dup_c1) or dup_c1.attrs["camera_subdivide"].get("parent_uuid") in ("", None)
    assert not is_coverage_leftover(a)
    assert is_shot_child(b)


def test_detach_skips_short_cell_that_is_not_film_opening() -> None:
    p1 = "1" * 24
    p2 = "2" * 24
    a = _fr(1, uuid=p1, role="vo_parent", shot_id="1-S1-K1", vo="открытие ролика", shot_index=1)
    b = _fr(
        2,
        uuid="x" * 24,
        role="shot",
        shot_id="1-S1-K2",
        parent_uuid=p1,
        vo="второй кусок",
        shot_index=2,
    )
    later = _fr(
        50,
        uuid=p2,
        role="vo_parent",
        shot_id="50-S9-K1",
        vo="научный ответ появился слишком поздно",
        shot_index=1,
    )
    later_c = _fr(
        51,
        uuid="y" * 24,
        role="shot",
        shot_id="50-S9-K2",
        parent_uuid=p2,
        vo="второй кусок",
        shot_index=2,
    )
    n = detach_duplicate_vo_replica_cells([a, b, later, later_c])
    assert n == 0
    assert not is_coverage_leftover(later)


def test_leftover_later_duplicate_vo_hides_copy_without_png(tmp_path: Path) -> None:
    from app.services.vo_shot_expand import leftover_later_duplicate_vo_frames

    scenes = tmp_path / "scenes"
    scenes.mkdir()
    (scenes / "frame_065_abc.png").write_bytes(b"png")
    p1 = "a" * 24
    p2 = "b" * 24
    first = _fr(
        65,
        uuid=p1,
        role="vo_parent",
        shot_id="65-S10-K1",
        vo="Когда результаты анализа указали на Ткача, стало очевидно,",
        shot_index=1,
    )
    copy_empty = _fr(
        129,
        uuid=p2,
        role="shot",
        shot_id="126-S18-K4",
        parent_uuid="c" * 24,
        vo="Когда результаты анализа указали на Ткача, стало очевидно,",
        shot_index=4,
    )
    copy_png = _fr(
        131,
        uuid="d" * 24,
        role="vo_parent",
        shot_id="131-S19-K1",
        vo="Когда результаты анализа указали на Ткача, стало очевидно,",
        shot_index=1,
    )
    (scenes / "frame_131_xyz.png").write_bytes(b"png")
    n = leftover_later_duplicate_vo_frames([first, copy_empty, copy_png], scenes)
    assert n == 1
    assert not is_coverage_leftover(first)
    assert is_coverage_leftover(copy_empty)
    assert not is_coverage_leftover(copy_png)


def test_leftover_later_keeps_live_cell_children() -> None:
    """Одинаковый закадр у шотов одной ячейки — не leftover и не отрыв от родителя."""
    from app.services.vo_shot_expand import leftover_later_duplicate_vo_frames

    pu = "p" * 24
    vo = "следователь открывает папку"
    parent = _fr(1, uuid=pu, role="vo_parent", shot_id="1-S1-K1", vo=vo, shot_index=1)
    kid = _fr(
        2,
        uuid="c" * 24,
        role="shot",
        shot_id="1-S1-K2",
        parent_uuid=pu,
        coverage_kind="child",
        vo=vo,
        shot_index=2,
    )
    n = leftover_later_duplicate_vo_frames([parent, kid], None)
    assert n == 0
    assert not is_coverage_leftover(parent)
    assert not is_coverage_leftover(kid)
    assert kid.attrs["camera_subdivide"]["parent_uuid"] == pu
    assert is_shot_child(kid)


def test_apply_ordered_voiceover_to_stills(tmp_path: Path) -> None:
    from app.services.vo_shot_expand import apply_ordered_voiceover_to_stills

    scenes = tmp_path / "scenes"
    scenes.mkdir()
    (scenes / "frame_001_a.png").write_bytes(b"p")
    (scenes / "frame_003_b.png").write_bytes(b"p")
    a = _fr(1, uuid="a" * 24, role="vo_parent", shot_id="1-S1-K1", vo="старое", shot_index=1)
    gap = _fr(2, uuid="b" * 24, role="shot", shot_id="1-S1-K2", vo="дыра без png", shot_index=2)
    c = _fr(3, uuid="c" * 24, role="shot", shot_id="1-S1-K3", vo="тоже старое", shot_index=3)
    n = apply_ordered_voiceover_to_stills(
        [a, gap, c],
        ["Первая фраза QC.", "Вторая фраза QC."],
        scenes,
    )
    assert n == 2
    assert a.voiceover_text == "Первая фраза QC."
    assert gap.voiceover_text == "дыра без png"
    assert c.voiceover_text == "Вторая фраза QC."


def test_leftover_frame_skips_outsee_queue(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    fr = _fr(
        196,
        uuid="q" * 24,
        role="shot",
        shot_id="195-S34-K2",
        leftover=True,
        vo="кусок",
    )
    assert frame_needs_shot1_image(fr, scenes) is False
