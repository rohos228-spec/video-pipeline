"""Ревью #352 C: лестница не прячется из-за подстроки чужого закадра."""

from types import SimpleNamespace

from app.models import FrameStatus
from app.services.vo_shot_expand import duplicate_scene_ladder_frames


def _cell(number: int, uuid: str, shot_id: str, scene: int, vo: str) -> SimpleNamespace:
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


def test_substring_of_other_s_ladder_stays_live() -> None:
    plain_vo = "приказчик читает список душ и записывает новую"
    assert len(plain_vo) > 23
    s_vo = "утром " + plain_vo + " в книгу двора"
    assert plain_vo != s_vo
    assert plain_vo in s_vo
    plain = _cell(57, "a" * 24, "57-K1", 1, plain_vo)
    sladder = _cell(58, "b" * 24, "58-S1-K1", 1, s_vo)
    extra = duplicate_scene_ladder_frames([plain, sladder])
    assert plain not in extra
    assert sladder not in extra
    assert plain.voiceover_text == plain_vo


def test_exact_voiceover_still_marks_plain_ladder() -> None:
    vo = "он рассказывает дежурному о краже и пишет заявление"
    keep = _cell(8, "e" * 24, "3-S2-K1", 2, vo)
    stale = _cell(90, "f" * 24, "1-K7", 2, vo)
    extra = duplicate_scene_ladder_frames([keep, stale])
    assert stale in extra
    assert keep not in extra
    assert stale.voiceover_text == vo
