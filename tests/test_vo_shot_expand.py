"""Дробление VO-ячейки на шоты для группы script_frames_qc."""

from app.services.scene_design.camera_expand import split_text_into_parts
from app.services.vo_shot_expand import shots_needed_for_vo, vo_duration_sec


def test_apply_shot_meta_keeps_scene_chain() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import _apply_shot_meta, looks_like_scene_chain

    chain = (
        "1. следственный отдел — отдел целиком\n"
        "(Сергей Ткач парадокс)\n"
        "2. комната Ткача — листает инструкции\n"
        "(которые он прекрасно знал изнутри.)"
    )
    assert looks_like_scene_chain(chain)
    fr = SimpleNamespace(attrs={"main_action": chain})
    _apply_shot_meta(
        fr,
        {"место": "следственный отдел", "действие": "отдел целиком", "план": "ОБЩИЙ"},
    )
    assert fr.attrs["main_action"] == chain
    assert fr.attrs["shot01_action"] == "отдел целиком"
    assert fr.attrs["place"] == "следственный отдел"


def test_apply_shot_meta_sets_action_when_empty() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import _apply_shot_meta

    fr = SimpleNamespace(attrs={})
    _apply_shot_meta(fr, {"действие": "у доски сверяют папки"})
    assert fr.attrs["main_action"] == "у доски сверяют папки"
    assert fr.attrs["shot01_action"] == "у доски сверяют папки"


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


def test_resolve_shot_plan_uses_kadrы() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import (
        planned_shots_from_attrs,
        resolve_shot_plan,
    )

    vo = "Ткач родился в Киселёвске, прошёл службу в армии, после чего начал карьеру в милиции."
    planned = [
        {"порядок": 1, "закадр": "Ткач родился в Киселёвске,", "план": "ОБЩИЙ"},
        {"порядок": 2, "закадр": "прошёл службу в армии,", "план": "СРЕДНИЙ"},
        {
            "порядок": 3,
            "закадр": "после чего начал карьеру в милиции.",
            "план": "СРЕДНИЙ",
        },
    ]
    fr = SimpleNamespace(attrs={"кадры": planned})
    assert planned_shots_from_attrs(fr) == planned
    need, parts = resolve_shot_plan(vo, planned)
    assert need == 3
    assert parts == [p["закадр"] for p in planned]
    assert "".join(parts).replace(" ", "") == vo.replace(" ", "")


def test_resolve_shot_plan_falls_back_without_kadrы() -> None:
    from app.services.vo_shot_expand import resolve_shot_plan

    vo = "Первая фраза тут. Вторая фраза здесь."
    need, parts = resolve_shot_plan(vo, [])
    assert need == 2
    assert len(parts) == 2


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


def test_cut_duplicate_leaves_split_vo_intact() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import apply_vo_shot_cuts

    pu = "aa" * 12
    cell = "Первая фраза. Вторая фраза."
    parent = SimpleNamespace(
        uuid=pu,
        voiceover_text=cell,
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
        voiceover_text="",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "shot_index": 2,
            }
        },
    )
    n = apply_vo_shot_cuts([parent, child])
    assert n == 2
    assert parent.voiceover_text == cell
    assert child.voiceover_text == ""
    p_shot = parent.attrs["camera_subdivide"]["vo_shot"]
    c_shot = child.attrs["camera_subdivide"]["vo_shot"]
    assert p_shot
    assert c_shot
    assert p_shot != cell
    assert " ".join((p_shot + " " + c_shot).split()) == " ".join(cell.split())


def test_apply_shot_voiceover_writes_cell_parts() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import apply_shot_voiceover_to_cells

    pu = "aa" * 12
    full = "Дарья Салтыкова. История началась не с процесса, а с двух жалоб."
    parent = SimpleNamespace(
        uuid=pu,
        voiceover_text=full,
        attrs={
            "кадры": [
                {"id": "1-K1", "закадр": "Дарья Салтыкова. "},
                {
                    "id": "1-K2",
                    "закадр": "История началась не с процесса, а с двух жалоб.",
                },
            ],
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "shot_index": 1,
            },
        },
    )
    child = SimpleNamespace(
        uuid="bb" * 12,
        voiceover_text="",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "shot_index": 2,
            }
        },
    )
    n = apply_shot_voiceover_to_cells([parent, child])
    assert n == 2
    assert parent.voiceover_text == "Дарья Салтыкова."
    assert child.voiceover_text.startswith("История началась")
    assert parent.attrs["vo_cell_full"] == full


def test_distribute_coverage_prompts_fills_child_shot2() -> None:
    from types import SimpleNamespace

    from app.services.plan_shot2 import SHOT2_PROMPT_ATTR, SHOT2_STATUS_ATTR
    from app.services.vo_shot_expand import distribute_coverage_prompts

    pu = "aa" * 12
    parent = SimpleNamespace(
        uuid=pu,
        image_prompt="master prompt",
        animation_prompt="",
        attrs={
            "image_prompt_shot2": "child closer",
            "промты_детей": [
                {"промт_картинки": "child closer", "промт_видео": "child move"}
            ],
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "shot_index": 1,
            },
        },
    )
    child = SimpleNamespace(
        uuid="bb" * 12,
        image_prompt="SHOULD CLEAR",
        animation_prompt="",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "shot_index": 2,
            }
        },
    )
    n = distribute_coverage_prompts([parent, child])
    assert n == 1
    assert child.image_prompt == ""
    assert child.attrs[SHOT2_PROMPT_ATTR] == "child closer"
    assert child.attrs[SHOT2_STATUS_ATTR] == "image_prompt_ready"
    assert child.animation_prompt == "child move"
    assert parent.attrs[SHOT2_STATUS_ATTR] == "skipped"
