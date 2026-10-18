"""Ревью #352: префикс 40 символов не детачит ячейку и не срезает лестницу."""

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
    parent_uuid: str = "",
    vo: str,
    kadry: list[dict],
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
                "parent_uuid": parent_uuid or uuid,
                "shot_index": shot_index,
            },
            "кадры": kadry,
        },
    )


def test_prefix40_does_not_detach_or_slice_ladder() -> None:
    opening = "Крепостной крестьянин полностью зависел от помещика"
    assert len(opening) >= 40
    later = opening[:40] + " и потом уехал в город со своей семьёй насовсем"
    p1, p2 = "1" * 24, "2" * 24
    ladder = [
        {"id": "50-K1", "закадр": later, "действие": "уехал"},
        {"id": "50-K2", "закадр": "семья на телеге", "действие": "телега"},
    ]
    first = _fr(
        1, uuid=p1, role="vo_parent", shot_id="1-K1",
        vo=opening, kadry=[{"id": "1-K1", "закадр": opening}], shot_index=1,
    )
    second = _fr(
        50, uuid=p2, role="vo_parent", shot_id="50-K1",
        vo=later, kadry=[dict(s) for s in ladder], shot_index=1,
    )
    n = detach_duplicate_vo_replica_cells([first, second])
    assert n == 0
    assert not is_coverage_leftover(second)
    assert [s["id"] for s in second.attrs["кадры"]] == ["50-K1", "50-K2"]
    assert second.voiceover_text == later


def test_exact_replica_keeps_full_ladder() -> None:
    vo = "открытие ролика"
    p1, p2 = "1" * 24, "2" * 24
    ladder = [
        {"id": "9-K1", "закадр": vo},
        {"id": "9-K2", "закадр": "второй кусок"},
    ]
    first = _fr(
        1, uuid=p1, role="vo_parent", shot_id="1-K1",
        vo=vo, kadry=[{"id": "1-K1", "закадр": vo}], shot_index=1,
    )
    dup = _fr(
        10, uuid=p2, role="vo_parent", shot_id="9-K1",
        vo=vo, kadry=[dict(s) for s in ladder], shot_index=1,
    )
    n = detach_duplicate_vo_replica_cells([first, dup])
    assert n == 1
    assert is_coverage_leftover(dup)
    assert [s["id"] for s in dup.attrs["кадры"]] == ["9-K1", "9-K2"]
    assert [s["id"] for s in dup.attrs["кадры_archived"]] == ["9-K1", "9-K2"]
    assert dup.attrs["camera_subdivide"].get("shots_in_beat") != 1
    assert dup.voiceover_text == vo
