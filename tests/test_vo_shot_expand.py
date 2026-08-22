"""Дробление VO-ячейки на шоты для группы script_frames_qc."""

from app.services.scene_design.camera_expand import split_text_into_parts
from app.services.vo_shot_expand import shots_needed_for_vo, vo_duration_sec


def test_shots_needed_splits_sentences() -> None:
    text = "Первая фраза тут. Вторая фраза здесь. Третья уже конец."
    assert shots_needed_for_vo(text) == 3


def test_shots_needed_two_when_one_long_clause() -> None:
    text = "История Дарьи Салтыковой началась не с громкого судебного процесса"
    assert shots_needed_for_vo(text) == 2


def test_shots_needed_one_when_short() -> None:
    assert shots_needed_for_vo("Коротко.") == 1


def test_vo_parts_sum_equals_cell() -> None:
    text = "Дарья Салтыкова История началась не с процесса, а с двух жалоб крестьян."
    n = shots_needed_for_vo(text)
    parts = split_text_into_parts(text, n)
    assert " ".join(parts).split() == text.split()


def test_vo_duration_at_least_two_sec_per_shot() -> None:
    assert vo_duration_sec("аб", shots=2) >= 4.0


def test_repair_glues_fragments_back_to_parent() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import repair_split_vo_on_parents

    pu = "aa" * 12
    parent = SimpleNamespace(
        uuid=pu,
        voiceover_text="Первая фраза.",
        attrs={
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "shot_index": 1,
            }
        },
    )
    child = SimpleNamespace(
        uuid="bb" * 12,
        voiceover_text="Вторая фраза.",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "shot_index": 2,
            }
        },
    )
    n = repair_split_vo_on_parents([parent, child])
    assert n == 1
    assert parent.voiceover_text == "Первая фраза. Вторая фраза."
    assert child.voiceover_text == ""
