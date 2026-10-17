"""Ревью #352 A: huge без полного закадра не уводит ячейку в leftover."""

from types import SimpleNamespace

from app.models import FrameStatus
from app.services.vo_shot_expand import (
    detach_duplicate_vo_replica_cells,
    is_coverage_leftover,
)


def _fr(
    number: int,
    *,
    uuid: str,
    role: str,
    shot_id: str,
    parent_uuid: str,
    vo: str,
    shot_index: int,
) -> SimpleNamespace:
    return SimpleNamespace(
        number=number,
        uuid=uuid,
        voiceover_text=vo,
        image_prompt="p",
        status=FrameStatus.image_prompt_ready,
        attrs={
            "camera_subdivide": {
                "role": role,
                "shot_id": shot_id,
                "parent_uuid": parent_uuid,
                "shot_index": shot_index,
            },
            "кадры": [{"id": shot_id, "закадр": vo}],
        },
    )


def test_huge_overlap_without_full_vo_keeps_children() -> None:
    p1, p2 = "1" * 24, "2" * 24
    opening = "первая ячейка рассказывает про помещика и двор целиком"
    later = "другая живая ячейка про телегу и отъезд семьи в город насовсем"
    assert opening != later
    shared = [
        "фраза альфа про двор",
        "фраза бета про книгу",
        "фраза гамма про печать",
        "фраза дельта про телегу",
    ]
    frames = [
        _fr(1, uuid=p1, role="vo_parent", shot_id="1-K1", parent_uuid=p1, vo=opening, shot_index=1),
    ]
    for i, piece in enumerate(shared, start=2):
        frames.append(
            _fr(
                i, uuid=f"{i:024x}", role="shot", shot_id=f"1-K{i}",
                parent_uuid=p1, vo=piece, shot_index=i,
            )
        )
    frames.append(
        _fr(50, uuid=p2, role="vo_parent", shot_id="50-K1", parent_uuid=p2, vo=later, shot_index=1)
    )
    children = []
    for i in range(15):
        uid = f"{i + 10:024x}"
        child = _fr(
            51 + i,
            uuid=uid,
            role="shot",
            shot_id=f"50-K{i + 2}",
            parent_uuid=p2,
            vo=shared[i % len(shared)],
            shot_index=i + 2,
        )
        children.append(child)
        frames.append(child)
    n = detach_duplicate_vo_replica_cells(frames)
    assert n == 0
    assert not is_coverage_leftover(frames[-1])
    for child in children:
        assert not is_coverage_leftover(child)
        assert child.attrs["camera_subdivide"]["parent_uuid"] == p2
        assert child.voiceover_text


def test_full_vo_match_still_unlinks_children() -> None:
    vo = "открытие ролика тем же полным закадром"
    p1, p2 = "a" * 24, "b" * 24
    first = _fr(1, uuid=p1, role="vo_parent", shot_id="1-K1", parent_uuid=p1, vo=vo, shot_index=1)
    dup = _fr(9, uuid=p2, role="vo_parent", shot_id="9-K1", parent_uuid=p2, vo=vo, shot_index=1)
    child = _fr(
        10, uuid="c" * 24, role="shot", shot_id="9-K2",
        parent_uuid=p2, vo="второй кусок", shot_index=2,
    )
    n = detach_duplicate_vo_replica_cells([first, dup, child])
    assert n == 2
    assert is_coverage_leftover(dup)
    assert is_coverage_leftover(child)
    assert child.attrs["camera_subdivide"]["parent_uuid"] == ""
    assert child.voiceover_text == "второй кусок"
    assert not is_coverage_leftover(first)
