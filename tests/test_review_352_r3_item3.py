"""Ревью #352: лестницы одной сцены из разных ячеек не уходят в leftover."""

from types import SimpleNamespace

from app.models import FrameStatus
from app.services.vo_shot_expand import duplicate_scene_ladder_frames


def _cell(
    number: int, uuid: str, shot_id: str, scene: int, vo: str
) -> SimpleNamespace:
    return SimpleNamespace(
        number=number,
        uuid=uuid,
        voiceover_text=vo,
        image_prompt="",
        status=FrameStatus.planned,
        attrs={
            "кадры": [{"id": shot_id, "сцена": scene, "закадр": vo}],
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": uuid,
                "shot_index": 1,
                "сцена": scene,
                "shot_id": shot_id,
            },
        },
    )


def test_same_scene_number_in_other_cell_stays() -> None:
    plain = _cell(
        57, "a" * 24, "57-K1", 1,
        "крепостной стоит во дворе и слушает помещика целиком",
    )
    sladder = _cell(
        58, "b" * 24, "58-S1-K1", 1,
        "приказчик читает список и записывает новую душу в книгу",
    )
    extra = duplicate_scene_ladder_frames([plain, sladder])
    assert plain not in extra
    assert sladder not in extra


def test_same_cell_prefix_still_drops_plain_duplicate() -> None:
    vo = "лимонный сок на столе и забытые очки следователя"
    keep = _cell(3, "c" * 24, "1-S3-K1", 3, vo)
    stale = _cell(4, "d" * 24, "1-K7", 3, vo)
    extra = duplicate_scene_ladder_frames([keep, stale])
    assert stale in extra
    assert keep not in extra


def test_renumber_prefix_drops_only_duplicate_voiceover() -> None:
    vo = "он рассказывает дежурному о краже и пишет заявление"
    keep = _cell(8, "e" * 24, "3-S2-K1", 2, vo)
    stale = _cell(90, "f" * 24, "1-K7", 2, vo)
    other = _cell(
        12, "9" * 24, "12-K1", 2,
        "другая ячейка той же цифры сцены про телегу и семью",
    )
    extra = duplicate_scene_ladder_frames([keep, stale, other])
    assert stale in extra
    assert keep not in extra
    assert other not in extra
