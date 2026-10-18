"""Ревью #352 B: QC-синк кладёт на кадры только живые шоты."""

from types import SimpleNamespace

from app.models import FrameStatus
from app.services.vo_shot_expand import sync_qc_kadry_onto_coverage_frames


def _fr(number: int, uuid: str, role: str, parent: str, shot_index: int, kadry, scene: int):
    return SimpleNamespace(
        number=number,
        uuid=uuid,
        voiceover_text="кусок",
        image_prompt="",
        status=FrameStatus.planned,
        attrs={
            "кадры": kadry,
            "camera_subdivide": {
                "role": role,
                "parent_uuid": parent,
                "shot_index": shot_index,
                "scene_split": 1,
                "сцена": scene,
                "shot_id": kadry[0]["id"],
            },
        },
    )


def test_sync_skips_marked_shot_and_keeps_it_on_parent() -> None:
    pu = "a" * 24
    ladder = [
        {"id": "1-S1-K1", "сцена": 1, "план": "ОБЩИЙ", "действие": "вошёл в архив"},
        {
            "id": "1-S1-K2",
            "сцена": 1,
            "план": "ДЕТАЛЬ",
            "действие": "помеченный шот",
            "активен": False,
            "закадр": "этот закадр помечен",
        },
        {"id": "1-S1-K3", "сцена": 1, "план": "СРЕДНИЙ", "действие": "открыл папку"},
        {"id": "1-S2-K1", "сцена": 2, "план": "КРУПНЫЙ", "действие": "печать на столе"},
    ]
    parent = _fr(1, pu, "vo_parent", pu, 1, [dict(s) for s in ladder], 1)
    child = _fr(
        2, "b" * 24, "shot", pu, 2,
        [dict(ladder[0]), dict(ladder[1])],
        1,
    )
    sync_qc_kadry_onto_coverage_frames([parent, child])
    ids = [s["id"] for s in parent.attrs["кадры"]]
    assert ids == ["1-S1-K1", "1-S1-K2", "1-S1-K3", "1-S2-K1"]
    marked = next(s for s in parent.attrs["кадры"] if s["id"] == "1-S1-K2")
    assert marked["закадр"] == "этот закадр помечен"
    assert marked.get("активен") is False
    mirror = child.attrs["кадры"]
    assert [s["id"] for s in mirror] == ["1-S1-K3"]
    assert str(child.attrs.get("shot01_action") or "") == "открыл папку"
    assert child.voiceover_text == "кусок"
