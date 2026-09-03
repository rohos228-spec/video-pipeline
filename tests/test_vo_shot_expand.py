"""Дробление VO-ячейки на шоты для группы script_frames_qc."""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base
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


def test_inherit_camera_copies_move_set_to_child() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import inherit_camera_on_children

    pu = "p" * 24
    parent = SimpleNamespace(
        uuid=pu,
        attrs={
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "крупность": "ОБЩИЙ",
                "движение": "панорама",
                "набор": "господский дом",
            }
        },
    )
    child = SimpleNamespace(
        uuid="c" * 24,
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "крупность": "ДЕТАЛЬ",
            }
        },
    )
    assert inherit_camera_on_children([parent, child]) == 1
    cs = child.attrs["camera_subdivide"]
    assert cs["движение"] == "панорама"
    assert cs["набор"] == "господский дом"


def test_inherit_camera_orphan_uses_place_as_set() -> None:
    """Родитель удалён — набор из место, движение не оставляем пустым."""
    from types import SimpleNamespace

    from app.services.vo_shot_expand import inherit_camera_on_children

    child = SimpleNamespace(
        uuid="c" * 24,
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": "d" * 24,
                "крупность": "ДЕТАЛЬ",
                "место": "господский дом",
            }
        },
    )
    assert inherit_camera_on_children([child]) == 1
    cs = child.attrs["camera_subdivide"]
    assert cs["набор"] == "господский дом"
    assert str(cs.get("движение") or "").strip()


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
    assert need == 1
    assert parts == [vo]


def test_kadry_are_scene_shots_tx_not_bit_stubs() -> None:
    from app.services.vo_shot_expand import kadry_are_scene_shots

    stubs = [
        {"id": "B01-K1", "parent_id": None, "порядок": 1, "действие": "ищут"},
        {"id": "B02-K1", "parent_id": None, "порядок": 2, "действие": "рождается"},
    ]
    assert kadry_are_scene_shots(stubs) is False
    assert kadry_are_scene_shots([]) is False
    tx = [
        {
            "id": "1-K1",
            "шаблон": "T2",
            "план": "ОБЩИЙ",
            "ракурс": "фронт",
            "место": "следственный отдел",
            "действие": "к доске крепят карточку",
        }
    ]
    assert kadry_are_scene_shots(tx) is True
    plan_only = [{"план": "СРЕДНИЙ", "место": "комната Ткача"}]
    assert kadry_are_scene_shots(plan_only) is True


def test_strip_non_scene_kadry_keeps_tx() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import (
        frame_has_scene_shots,
        strip_non_scene_kadry,
    )

    stub = SimpleNamespace(
        attrs={"кадры": [{"id": "B01-K1", "действие": "ищут"}]}
    )
    assert strip_non_scene_kadry(stub) is True
    assert "кадры" not in stub.attrs
    tx = SimpleNamespace(
        attrs={
            "кадры": [
                {
                    "id": "1-K1",
                    "шаблон": "T3",
                    "план": "ОБЩИЙ",
                    "место": "архив",
                }
            ]
        }
    )
    assert strip_non_scene_kadry(tx) is False
    assert frame_has_scene_shots(tx) is True


def test_collect_bits_for_reseed_merges_one_per_cell() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import collect_bits_for_reseed

    cells = [
        SimpleNamespace(attrs={"биты": [{"порядок": 1, "глагол": "a"}]}),
        SimpleNamespace(attrs={"биты": [{"порядок": 2, "глагол": "b"}]}),
    ]
    merged = collect_bits_for_reseed(cells)
    assert [b["глагол"] for b in merged] == ["a", "b"]
    long = SimpleNamespace(
        attrs={
            "биты": [
                {"порядок": 1, "глагол": "x"},
                {"порядок": 2, "глагол": "y"},
            ]
        }
    )
    short = SimpleNamespace(attrs={"биты": [{"порядок": 1, "глагол": "z"}]})
    assert collect_bits_for_reseed([long, short])[0]["глагол"] == "x"


def test_canvas_four_node_has_no_fw_shots() -> None:
    from types import SimpleNamespace

    from app.orchestrator.steps.enrich_xlsx import _canvas_has_fw_shots

    four = SimpleNamespace(
        meta={
            "canvas_graph": {
                "nodes": [
                    {"id": "n_excel_gpt_fw_script"},
                    {"id": "n_excel_gpt_fw_check_script"},
                    {"id": "n_excel_gpt_fw_frames"},
                    {"id": "n_excel_gpt_fw_qc"},
                ]
            }
        }
    )
    six = SimpleNamespace(
        meta={
            "canvas_graph": {
                "nodes": [
                    {"id": "n_excel_gpt_fw_script"},
                    {"id": "n_excel_gpt_fw_action"},
                    {"id": "n_excel_gpt_fw_shots"},
                    {"id": "n_excel_gpt_fw_frames"},
                ]
            }
        }
    )
    assert _canvas_has_fw_shots(four) is False
    assert _canvas_has_fw_shots(six) is True

    from app.services.node_groups import canvas_has_script_frames_qc

    assert canvas_has_script_frames_qc(four) is True
    assert canvas_has_script_frames_qc(six) is True
    assert canvas_has_script_frames_qc(SimpleNamespace(meta={"canvas_graph": {"nodes": []}})) is False


def test_safe_upload_node_key_strips_windows_colon(tmp_path) -> None:
    from app.orchestrator.steps.enrich_xlsx import _safe_upload_node_key

    safe = _safe_upload_node_key("n_excel_gpt_fw_frames:action_chain")
    assert ":" not in safe
    assert _safe_upload_node_key("n_excel_gpt_fw_frames") == "n_excel_gpt_fw_frames"
    p = tmp_path / safe
    p.mkdir(parents=True, exist_ok=True)
    assert p.is_dir()


def test_group_prompts_for_four_node_markup_exist() -> None:
    from app.orchestrator.steps.enrich_xlsx import _load_script_frames_qc_prompt

    action = _load_script_frames_qc_prompt("main_action_from_bits_ru")
    shots = _load_script_frames_qc_prompt("scenes_to_frames_ru")
    assert "главное_действие" in action
    assert "select" in shots.casefold() or "шаблон" in shots


def test_kadry_from_bits_covers_full_vo() -> None:
    from app.services.vo_shot_expand import kadry_from_bits

    vo = (
        "Сергей Ткач Самый страшный парадокс этой истории заключается в том, "
        "что Сергея Ткача искали по тем же правилам. "
        "Для следствия он оставался неизвестным преступником. "
        "Ткач родился в Киселёвске."
    )
    bits = [
        {"порядок": 1, "глагол": "раскрывает", "якорь": "искали по тем же правилам"},
        {
            "порядок": 2,
            "глагол": "противопоставляет",
            "якорь": "оставался неизвестным преступником",
        },
        {"порядок": 3, "глагол": "рождается", "якорь": "Ткач родился в Киселёвске"},
    ]
    planned = kadry_from_bits(vo, bits)
    assert len(planned) == 3
    joined = " ".join(item["закадр"] for item in planned)
    assert joined.split() == vo.split()
    assert planned[0]["id"] == "B01-K1"
    assert planned[1]["id"] == "B02-K1"
    assert planned[1]["parent_id"] is None


def test_bit_kadry_do_not_collapse_as_coverage() -> None:
    from app.services.vo_shot_expand import (
        flattened_coverage_groups,
        kadry_from_bits,
    )

    vo = "Первая фраза целиком. Вторая тоже на месте. Третья закрывает."
    bits = [
        {"порядок": 1, "якорь": "Первая фраза"},
        {"порядок": 2, "якорь": "Вторая тоже"},
        {"порядок": 3, "якорь": "Третья закрывает"},
    ]
    planned = kadry_from_bits(vo, bits)
    frames = [_kadry_frame(i + 1, planned[i]["id"]) for i in range(3)]
    for fr, shot in zip(frames, planned, strict=True):
        fr.voiceover_text = shot["закадр"]
        fr.attrs["кадры"][0]["закадр"] = shot["закадр"]
    assert flattened_coverage_groups(frames) == {}


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


def test_kadry_partition_rejects_dangling() -> None:
    from app.services.vo_shot_expand import kadry_vo_partition

    full = "Его записывали как «душу», продавали вместе с землёй."
    assert (
        kadry_vo_partition(
            full,
            [
                {"закадр": "Его записывали как «душу», продавали вместе с"},
                {"закадр": "землёй."},
            ],
        )
        is None
    )


def test_kadry_partition_requires_full_cover() -> None:
    from app.services.vo_shot_expand import kadry_vo_partition

    full = "Первая фраза целиком. Вторая тоже на месте."
    ok = kadry_vo_partition(
        full,
        [
            {"закадр": "Первая фраза целиком."},
            {"закадр": "Вторая тоже на месте."},
        ],
    )
    assert ok == ["Первая фраза целиком.", "Вторая тоже на месте."]
    assert kadry_vo_partition(full, [{"закадр": "Первая фраза целиком."}]) is None


def test_apply_shot_voiceover_uses_clauses_not_word_split() -> None:
    from types import SimpleNamespace

    from app.services.scene_design.camera_expand import vo_chunk_is_dangling
    from app.services.vo_shot_expand import apply_shot_voiceover_to_cells

    pu = "cc" * 12
    full = "Его записывали как «душу», продавали вместе с землёй, переселяли."
    kadry = [
        {"id": "1-K1", "закадр": "Его записывали как «душу», продавали вместе с"},
        {"id": "1-K2", "закадр": "землёй, переселяли."},
    ]
    parent = SimpleNamespace(
        uuid=pu,
        voiceover_text=full,
        attrs={
            "кадры": kadry,
            "vo_cell_full": full,
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "shot_index": 1,
            },
        },
    )
    child = SimpleNamespace(
        uuid="dd" * 12,
        voiceover_text="",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "shot_index": 2,
            }
        },
    )
    apply_shot_voiceover_to_cells([parent, child])
    assert not vo_chunk_is_dangling(parent.voiceover_text)
    assert not vo_chunk_is_dangling(child.voiceover_text)
    assert " ".join(
        (parent.voiceover_text + " " + child.voiceover_text).split()
    ) == " ".join(full.split())


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
    assert n >= 2
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


def test_distribute_coverage_prompts_all_children_from_list() -> None:
    """K2 и K3 равноправны: каждый берёт свой элемент промты_детей, не _2."""
    from types import SimpleNamespace

    from app.services.plan_shot2 import SHOT2_PROMPT_ATTR
    from app.services.vo_shot_expand import distribute_coverage_prompts

    pu = "aa" * 12
    parent = SimpleNamespace(
        uuid=pu,
        image_prompt="master",
        animation_prompt="",
        attrs={
            "промты_детей": [
                {"промт_картинки": "k2 img", "промт_видео": "k2 vid"},
                {"промт_картинки": "k3 img", "промт_видео": "k3 vid"},
            ],
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "shot_index": 1,
            },
        },
    )
    child2 = SimpleNamespace(
        uuid="bb" * 12,
        image_prompt="",
        animation_prompt="",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "shot_index": 2,
            }
        },
    )
    child3 = SimpleNamespace(
        uuid="cc" * 12,
        image_prompt="",
        animation_prompt="",
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "shot_index": 3,
            }
        },
    )
    n = distribute_coverage_prompts([parent, child2, child3])
    assert n == 2
    assert child2.attrs[SHOT2_PROMPT_ATTR] == "k2 img"
    assert child2.animation_prompt == "k2 vid"
    assert child3.attrs[SHOT2_PROMPT_ATTR] == "k3 img"
    assert child3.animation_prompt == "k3 vid"


def test_promote_shots_makes_one_cell_per_kadr() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import promote_shots_to_vo_cells

    pu = "aa" * 12
    cu = "bb" * 12
    xu = "cc" * 12
    full = "Первая фраза целиком. Вторая тоже на месте."
    kadry = [
        {"id": "1-K1", "закадр": "мусор", "действие": "жест", "место": "двор"},
        {"id": "1-K2", "закадр": "мусор", "действие": "деталь", "место": "двор"},
    ]
    parent = SimpleNamespace(
        uuid=pu,
        voiceover_text=full,
        image_prompt="master",
        animation_prompt="",
        duration_seconds=4.0,
        attrs={
            "кадры": kadry,
            "vo_cell_full": full,
            "биты": [{"порядок": 1}],
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "shot_index": 1,
            },
        },
    )
    child = SimpleNamespace(
        uuid=cu,
        voiceover_text="",
        image_prompt="",
        animation_prompt="",
        duration_seconds=2.0,
        attrs={
            "image_prompt_shot2": "closer",
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "shot_index": 2,
            },
        },
    )
    spare = SimpleNamespace(
        uuid=xu,
        voiceover_text="",
        image_prompt="",
        animation_prompt="",
        duration_seconds=2.0,
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "shot_index": 3,
            }
        },
    )
    n, extra = promote_shots_to_vo_cells([parent, child, spare])
    assert n == 2
    assert extra == [spare]
    assert parent.voiceover_text.startswith("Первая фраза")
    assert child.voiceover_text.startswith("Вторая")
    assert parent.attrs["vo_cell_full"] == parent.voiceover_text
    assert child.attrs["vo_cell_full"] == child.voiceover_text
    assert parent.attrs["camera_subdivide"]["parent_uuid"] == pu
    assert child.attrs["camera_subdivide"]["parent_uuid"] == cu
    assert parent.attrs["camera_subdivide"]["role"] == "vo_parent"
    assert child.attrs["camera_subdivide"]["role"] == "vo_parent"
    assert child.image_prompt == "closer"
    assert "биты" not in child.attrs
    assert len(parent.attrs["кадры"]) == 1
    assert len(child.attrs["кадры"]) == 1
    assert " ".join(
        (parent.voiceover_text + " " + child.voiceover_text).split()
    ) == " ".join(full.split())


def test_promote_is_idempotent_on_flat_cells() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import promote_shots_to_vo_cells

    pu = "aa" * 12
    cu = "bb" * 12
    full = "Первая фраза целиком. Вторая тоже на месте."
    parent = SimpleNamespace(
        uuid=pu,
        voiceover_text=full,
        image_prompt="",
        animation_prompt="",
        duration_seconds=4.0,
        attrs={
            "кадры": [
                {"id": "1-K1", "закадр": "x"},
                {"id": "1-K2", "закадр": "y"},
            ],
            "vo_cell_full": full,
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "shot_index": 1,
            },
        },
    )
    child = SimpleNamespace(
        uuid=cu,
        voiceover_text="",
        image_prompt="",
        animation_prompt="",
        duration_seconds=2.0,
        attrs={
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": pu,
                "shot_index": 2,
            }
        },
    )
    promote_shots_to_vo_cells([parent, child])
    first = (parent.voiceover_text, child.voiceover_text)
    n2, extra2 = promote_shots_to_vo_cells([parent, child])
    assert extra2 == []
    assert n2 == 2
    assert (parent.voiceover_text, child.voiceover_text) == first
    assert parent.attrs["camera_subdivide"]["parent_uuid"] == pu
    assert child.attrs["camera_subdivide"]["parent_uuid"] == cu


def test_promote_keeps_full_vo_without_child_slots() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import promote_shots_to_vo_cells

    pu = "aa" * 12
    full = "Первая фраза целиком. Вторая тоже на месте."
    parent = SimpleNamespace(
        uuid=pu,
        voiceover_text=full,
        image_prompt="",
        animation_prompt="",
        duration_seconds=4.0,
        attrs={
            "кадры": [
                {"id": "1-K1", "закадр": "x"},
                {"id": "1-K2", "закадр": "y"},
            ],
            "vo_cell_full": full,
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "shot_index": 1,
            },
        },
    )
    n, extra = promote_shots_to_vo_cells([parent])
    assert extra == []
    assert n == 1
    assert parent.voiceover_text == full


def test_enrich_xlsx_coverage_hook_includes_shots_frames_qc() -> None:
    from pathlib import Path

    src = Path("app/orchestrator/steps/enrich_xlsx.py").read_text(encoding="utf-8")
    assert 'endswith(("_fw_shots", "_fw_frames", "_fw_qc"))' in src
    assert "apply_shot_coverage_to_vo_cells" in src
    assert "collapse_flattened_coverage_cells" in src
    assert "_canvas_has_fw_shots(project)" in src


def _scene_split_fixture():
    """Seed: 4 кадра в 2 сценах + 3 слота от expand (role=shot)."""
    from types import SimpleNamespace

    pu, c2u, c3u, c4u = ("aa" * 12, "bb" * 12, "cc" * 12, "dd" * 12)
    full = (
        "Фраза один про двор. Фраза два про двор. "
        "Фраза три про архив. Фраза четыре про архив."
    )
    kadry = [
        {"id": "1-K1", "сцена": 1, "шаблон": "T3", "план": "ОБЩИЙ",
         "место": "двор", "действие": "входит в ворота", "parent_id": None},
        {"id": "1-K2", "сцена": 1, "шаблон": "T3", "план": "СРЕДНИЙ",
         "место": "двор", "действие": "снимает кепку", "parent_id": "1-K1"},
        {"id": "1-K3", "сцена": 2, "шаблон": "T2", "план": "ОБЩИЙ",
         "место": "архив", "действие": "вошёл между стеллажами", "parent_id": None},
        {"id": "1-K4", "сцена": 2, "шаблон": "T2", "план": "СРЕДНИЙ",
         "место": "архив", "действие": "тянет папку с полки", "parent_id": "1-K3"},
    ]
    chain = (
        "1. двор — входит в ворота\n(Фраза один про двор. Фраза два про двор.)\n"
        "2. архив — берёт папку с полки\n(Фраза три про архив. Фраза четыре про архив.)"
    )
    parent = SimpleNamespace(
        uuid=pu, voiceover_text=full, image_prompt="", animation_prompt="",
        duration_seconds=8.0,
        attrs={
            "кадры": [dict(s) for s in kadry],
            "vo_cell_full": full,
            "главное_действие": chain,
            "биты": [{"порядок": 1}],
            "camera_subdivide": {
                "role": "vo_parent", "parent_uuid": pu, "shot_index": 1,
            },
        },
    )

    def child(uid: str, idx: int):
        return SimpleNamespace(
            uuid=uid, voiceover_text="", image_prompt="", animation_prompt="",
            duration_seconds=2.0,
            attrs={"camera_subdivide": {
                "role": "shot", "parent_uuid": pu, "shot_index": idx,
            }},
        )

    return parent, [child(c2u, 2), child(c3u, 3), child(c4u, 4)], full


def test_promote_splits_by_scene_keeps_children() -> None:
    from app.services.vo_shot_expand import promote_shots_to_vo_cells

    parent, children, full = _scene_split_fixture()
    c2, c3, c4 = children
    n, extra = promote_shots_to_vo_cells([parent, *children])
    assert extra == []
    assert n == 4
    # Сцена 1: родитель + дочерний K2.
    pa = parent.attrs["camera_subdivide"]
    assert pa["role"] == "vo_parent" and pa["parent_uuid"] == parent.uuid
    assert pa["shots_in_beat"] == 2 and pa["scene_split"] == 1
    assert [s["id"] for s in parent.attrs["кадры"]] == ["1-K1", "1-K2"]
    assert parent.attrs["кадры"][0]["parent_id"] is None
    assert parent.attrs["кадры"][1]["parent_id"] == "1-K1"
    assert parent.voiceover_text == "Фраза один про двор."
    assert parent.attrs["vo_cell_full"] == "Фраза один про двор. Фраза два про двор."
    assert parent.attrs["главное_действие"].startswith("1. двор — входит в ворота")
    assert "биты" in parent.attrs  # первый родитель несёт биты
    # Дочерний кадр сцены 1.
    c2a = c2.attrs["camera_subdivide"]
    assert c2a["role"] == "shot" and c2a["parent_uuid"] == parent.uuid
    assert c2a["shot_index"] == 2 and c2a["shots_in_beat"] == 2
    assert c2.voiceover_text == "Фраза два про двор."
    assert [s["id"] for s in c2.attrs["кадры"]] == ["1-K2"]
    assert "главное_действие" not in c2.attrs
    # Сцена 2: новый родитель (новое место) + дочерний K4.
    c3a = c3.attrs["camera_subdivide"]
    assert c3a["role"] == "vo_parent" and c3a["parent_uuid"] == c3.uuid
    assert [s["id"] for s in c3.attrs["кадры"]] == ["1-K3", "1-K4"]
    assert c3.attrs["кадры"][0]["parent_id"] is None  # новое место — master
    assert c3.attrs["кадры"][1]["parent_id"] == "1-K3"
    assert c3.voiceover_text == "Фраза три про архив."
    assert c3.attrs["главное_действие"].startswith("2. архив — берёт папку")
    assert "биты" not in c3.attrs  # биты только на первом родителе
    c4a = c4.attrs["camera_subdivide"]
    assert c4a["role"] == "shot" and c4a["parent_uuid"] == c3.uuid
    assert c4.voiceover_text == "Фраза четыре про архив."
    # Склейка кусков всех ячеек = весь закадр исходной ячейки.
    joined = " ".join(
        (parent.voiceover_text, c2.voiceover_text,
         c3.voiceover_text, c4.voiceover_text)
    )
    assert " ".join(joined.split()) == " ".join(full.split())


def test_promote_does_not_give_next_scene_vo_to_shots() -> None:
    """Кадры сцены N берут только VO этой сцены, даже если GPT сдвинул закадр."""
    from types import SimpleNamespace

    from app.services.vo_shot_expand import promote_shots_to_vo_cells

    s1 = "Самые странные преступления в истории"
    s2 = (
        "Некоторые преступления поражают не столько своей жестокостью, "
        "сколько нелогичностью."
    )
    s3 = (
        "Обычно мотив, план и поведение преступника складываются "
        "в понятную цепочку."
    )
    full = f"{s1} {s2} {s3}"
    # GPT склеил куски в полный VO, но сдвиг: S1 забрал начало S2, S2 — S3.
    kadry = [
        {
            "id": "1-S1-K1",
            "сцена": 1,
            "шаблон": "T2",
            "закадр": f"{s1} Некоторые преступления поражают не столько своей жестокостью,",
        },
        {
            "id": "1-S2-K1",
            "сцена": 2,
            "шаблон": "T2",
            "закадр": "сколько нелогичностью. Обычно мотив,",
        },
        {
            "id": "1-S2-K2",
            "сцена": 2,
            "шаблон": "T2",
            "закадр": "план и поведение преступника складываются в понятную цепочку.",
        },
        {
            "id": "1-S3-K1",
            "сцена": 3,
            "шаблон": "T5",
            "закадр": "хвост",
        },
    ]
    chain = (
        f"1. архив — раскладывает папки\n({s1})\n"
        f"2. место преступления — фиксирует следы\n({s2})\n"
        f"3. кабинет — соединяет карточки\n({s3})"
    )
    pu = "aa" * 12
    parent = SimpleNamespace(
        uuid=pu,
        voiceover_text=full,
        image_prompt="",
        animation_prompt="",
        duration_seconds=8.0,
        attrs={
            "кадры": [dict(s) for s in kadry],
            "vo_cell_full": full,
            "главное_действие": chain,
            "биты": [{"порядок": 1}],
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": pu,
                "shot_index": 1,
            },
        },
    )
    children = [
        SimpleNamespace(
            uuid=f"{i:02x}" * 12,
            voiceover_text="",
            image_prompt="",
            animation_prompt="",
            duration_seconds=2.0,
            attrs={
                "camera_subdivide": {
                    "role": "shot",
                    "parent_uuid": pu,
                    "shot_index": i + 1,
                }
            },
        )
        for i in range(1, 4)
    ]
    promote_shots_to_vo_cells([parent, *children])
    by_sc: dict[int, list[str]] = {}
    for fr in [parent, *children]:
        for s in fr.attrs.get("кадры") or []:
            try:
                n = int(s.get("сцена"))
            except (TypeError, ValueError):
                continue
            vo = " ".join(str(s.get("закадр") or "").split())
            if vo:
                by_sc.setdefault(n, []).append(vo)
    # уникальные куски по сцене (родитель держит всю лестницу)
    def uniq(n: int) -> str:
        seen: list[str] = []
        for p in by_sc.get(n) or []:
            if p not in seen:
                seen.append(p)
        return " ".join(seen)

    assert s1 in uniq(1)
    assert s2.split()[0] not in uniq(1).split() or uniq(1) == s1
    assert " ".join(uniq(1).split()) == s1
    assert " ".join(uniq(2).split()) == s2
    assert " ".join(uniq(3).split()) == s3
    assert "цепочку" not in uniq(2)
    assert "жестокостью" not in uniq(1)


def test_planned_for_parent_expand_keeps_only_own_scene() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import planned_for_parent_expand

    planned = [
        {"id": "1-S1-K1", "сцена": 1},
        {"id": "1-S1-K2", "сцена": 1},
        {"id": "1-S2-K1", "сцена": 2},
        {"id": "1-S3-K1", "сцена": 3},
    ]
    parent = SimpleNamespace(
        attrs={
            "кадры": [dict(s) for s in planned],
            "camera_subdivide": {
                "role": "vo_parent",
                "scene_split": 1,
                "сцена": 1,
                "shot_id": "1-S1-K1",
            },
        }
    )
    assert [s["id"] for s in planned_for_parent_expand(parent, planned)] == [
        "1-S1-K1",
        "1-S1-K2",
    ]
    parent.attrs["camera_subdivide"].pop("scene_split")
    assert [s["id"] for s in planned_for_parent_expand(parent, planned)] == [
        "1-S1-K1",
        "1-S1-K2",
        "1-S2-K1",
        "1-S3-K1",
    ]


def test_promote_drops_stale_scene_ladder() -> None:
    """1-S3-K* остаётся, хвост старого expand 1-K7/K8 уходит в extra."""
    from types import SimpleNamespace

    from app.services.vo_shot_expand import promote_shots_to_vo_cells

    vo1, vo2 = "Лимонный сок,", "забытые очки."
    scene_vo = f"{vo1} {vo2}"
    chain = f"3. стол следствия — выкладывает улики\n({scene_vo})"

    def cell(
        uid: str,
        shots: list[tuple[str, str]],
        *,
        db_id: int,
        role: str = "vo_parent",
        parent_uid: str = "",
        shot_index: int = 1,
    ):
        kadry = [
            {"id": sid, "сцена": 3, "шаблон": "T9", "закадр": piece}
            for sid, piece in shots
        ]
        return SimpleNamespace(
            id=db_id,
            uuid=uid,
            voiceover_text=shots[0][1],
            image_prompt="",
            animation_prompt="",
            duration_seconds=2.0,
            attrs={
                "кадры": kadry if role == "vo_parent" else [kadry[0]],
                "vo_cell_full": scene_vo if role == "vo_parent" else shots[0][1],
                "главное_действие": chain if role == "vo_parent" else None,
                "camera_subdivide": {
                    "role": role,
                    "parent_uuid": parent_uid or uid,
                    "shot_index": shot_index,
                    "shots_in_beat": len(shots) if role == "vo_parent" else 2,
                    "scene_split": 1,
                    "сцена": 3,
                    "shot_id": shots[0][0],
                },
            },
        )

    current = cell("cc" * 12, [("1-S3-K1", vo1), ("1-S3-K2", vo2)], db_id=100)
    current_c = cell(
        "dd" * 12, [("1-S3-K2", vo2)], db_id=101, role="shot",
        parent_uid=current.uuid, shot_index=2,
    )
    stale = cell("ee" * 12, [("1-K7", vo1), ("1-K8", vo2)], db_id=50)
    stale_c = cell(
        "ff" * 12, [("1-K8", vo2)], db_id=51, role="shot",
        parent_uid=stale.uuid, shot_index=2,
    )
    _n, extra = promote_shots_to_vo_cells([current, current_c, stale, stale_c])
    assert stale in extra and stale_c in extra
    assert current not in extra and current_c not in extra


def test_promote_scene_split_is_idempotent() -> None:
    from app.services.vo_shot_expand import promote_shots_to_vo_cells

    parent, children, _full = _scene_split_fixture()
    frames = [parent, *children]
    promote_shots_to_vo_cells(frames)
    snap = [
        (f.voiceover_text, f.attrs["camera_subdivide"]["role"],
         f.attrs["camera_subdivide"]["parent_uuid"])
        for f in frames
    ]
    n2, extra2 = promote_shots_to_vo_cells(frames)
    assert extra2 == []
    assert n2 == 4
    assert [
        (f.voiceover_text, f.attrs["camera_subdivide"]["role"],
         f.attrs["camera_subdivide"]["parent_uuid"])
        for f in frames
    ] == snap
    assert [s["id"] for s in parent.attrs["кадры"]] == ["1-K1", "1-K2"]


def test_promote_drops_extra_frames_without_vo() -> None:
    """Клауз меньше кадров — лишние дети в extra, у оставшихся непустой VO."""
    from types import SimpleNamespace

    from app.services.vo_shot_expand import promote_shots_to_vo_cells

    pu = "aa" * 12
    full = "Короткая фраза. Вторая."
    kadry = [
        {"id": "1-K1", "сцена": 1, "шаблон": "T2", "место": "архив",
         "действие": "вошёл", "parent_id": None},
        {"id": "1-K2", "сцена": 1, "шаблон": "T2", "место": "архив",
         "действие": "тянет папку", "parent_id": "1-K1"},
        {"id": "1-K3", "сцена": 1, "шаблон": "T2", "место": "архив",
         "действие": "обложка крупно", "parent_id": "1-K1"},
        {"id": "1-K4", "сцена": 1, "шаблон": "T2", "место": "архив",
         "действие": "лицо понял", "parent_id": "1-K1"},
    ]
    parent = SimpleNamespace(
        uuid=pu, voiceover_text=full, image_prompt="", animation_prompt="",
        duration_seconds=4.0,
        attrs={"кадры": [dict(s) for s in kadry], "vo_cell_full": full,
               "camera_subdivide": {"role": "vo_parent", "parent_uuid": pu,
                                    "shot_index": 1}},
    )
    children = [
        SimpleNamespace(
            uuid=f"{i:02x}" * 12, voiceover_text="", image_prompt="",
            animation_prompt="", duration_seconds=1.0,
            attrs={"camera_subdivide": {"role": "shot", "parent_uuid": pu,
                                        "shot_index": i + 1}},
        )
        for i in range(1, 4)
    ]
    n, extra = promote_shots_to_vo_cells([parent, *children])
    assert extra
    kept = [parent, *[c for c in children if c not in extra]]
    assert n == len(kept)
    assert all((f.voiceover_text or "").strip() for f in kept)
    assert all(str(s.get("закадр") or "").strip() for s in parent.attrs["кадры"])
    joined = " ".join((f.voiceover_text or "") for f in kept)
    assert " ".join(joined.split()) == " ".join(full.split())


def test_kadry_vo_partition_aligned_recovers_gpt_drift() -> None:
    """Фрагменты GPT без запятых исходника — выравнивание режет по ним."""
    from app.services.vo_shot_expand import (
        kadry_vo_partition,
        kadry_vo_partition_aligned,
    )

    full = ("Михаил пришёл к отделению полиции, зашёл внутрь, "
            "рассказал дежурному о краже и написал заявление.")
    planned = [
        {"закадр": "Михаил пришёл к отделению полиции"},
        {"закадр": "зашёл внутрь"},
        {"закадр": "рассказал дежурному о краже"},
        {"закадр": "и написал заявление."},
    ]
    # Точная склейка ломается о запятые («полиции» vs «полиции,»).
    assert kadry_vo_partition(full, planned) is None
    parts = kadry_vo_partition_aligned(full, planned)
    assert parts is not None and len(parts) == 4
    assert " ".join(" ".join(parts).split()) == " ".join(full.split())


def test_coverage_parent_prefers_explicit_parent_id() -> None:
    from types import SimpleNamespace

    from app.services.vo_shot_expand import (
        coverage_parent_shot_id,
        is_coverage_child,
    )

    # scene-split: parent_id=null у master нового места → без референса.
    master = SimpleNamespace(
        attrs={
            "кадры": [{"id": "1-K3", "parent_id": None}],
            "camera_subdivide": {"role": "vo_parent", "scene_split": 1,
                                 "shot_id": "1-K3"},
        }
    )
    assert coverage_parent_shot_id(master) == ""
    assert not is_coverage_child(master)
    # Кросс-сценовый parent (то же место) → референс на явный parent_id.
    cont = SimpleNamespace(
        attrs={
            "кадры": [{"id": "1-K5", "parent_id": "1-K1"}],
            "camera_subdivide": {"role": "vo_parent", "scene_split": 1,
                                 "shot_id": "1-K5",
                                 "coverage_parent_id": "1-K1"},
        }
    )
    assert coverage_parent_shot_id(cont) == "1-K1"
    assert is_coverage_child(cont)
    # Легаси-flatten без флага: прежняя эвристика prefix-K1.
    legacy = SimpleNamespace(
        attrs={
            "кадры": [{"id": "1-K2", "parent_id": None}],
            "camera_subdivide": {"role": "vo_parent", "shot_id": "1-K2"},
        }
    )
    assert coverage_parent_shot_id(legacy) == "1-K1"
    assert is_coverage_child(legacy)


@pytest.fixture
async def mem_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _scope():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr("app.db.session_scope", _scope)
    yield _scope
    await engine.dispose()


async def test_apply_shot_coverage_splits_scenes_with_children(mem_db) -> None:
    """Хвост группы: seed → ячейки по сценам, дочерние шоты в БД."""
    import uuid as _uuid

    from sqlalchemy import select

    from app.models import Frame, Project, ProjectStatus
    from app.services.vo_shot_expand import (
        apply_shot_coverage_to_vo_cells,
        is_shot_child,
        planned_shots_from_attrs,
    )

    full = (
        "Михаил идёт к зданию полиции. Он заходит внутрь. "
        "Он рассказывает дежурному о краже. Он пишет заявление за столом."
    )
    chain = (
        "1. улица — Михаил идёт к зданию полиции\n"
        "(Михаил идёт к зданию полиции. Он заходит внутрь.)\n"
        "2. отделение полиции — пишет заявление за столом\n"
        "(Он рассказывает дежурному о краже. Он пишет заявление за столом.)"
    )
    kadry = [
        {"id": "1-K1", "сцена": 1, "шаблон": "T6", "план": "ОБЩИЙ",
         "ракурс": "фронт", "место": "улица", "действие": "идёт к зданию",
         "parent_id": None},
        {"id": "1-K2", "сцена": 1, "шаблон": "T6", "план": "ОБЩИЙ",
         "ракурс": "фронт", "место": "отделение полиции",
         "действие": "подходит к крыльцу", "parent_id": None},
        {"id": "1-K3", "сцена": 2, "шаблон": "T5", "план": "СРЕДНИЙ",
         "ракурс": "3/4", "место": "отделение полиции",
         "действие": "сидит, пишет", "parent_id": "1-K2"},
        {"id": "1-K4", "сцена": 2, "шаблон": "T5", "план": "ДЕТАЛЬ",
         "ракурс": "макро", "место": "отделение полиции",
         "действие": "рука выводит строки", "parent_id": "1-K2"},
    ]
    async with mem_db() as session:
        project = Project(
            slug=f"scenesplit-{_uuid.uuid4().hex[:8]}",
            topic="t",
            status=ProjectStatus.new,
            meta={},
        )
        session.add(project)
        await session.flush()
        seed = Frame(
            project_id=project.id,
            number=1,
            sort_key=1.0,
            uuid="aa" * 12,
            voiceover_text=full,
            duration_seconds=12.0,
            attrs={
                "кадры": [dict(s) for s in kadry],
                "vo_cell_full": full,
                "главное_действие": chain,
                "биты": [{"порядок": 1}],
                "camera_subdivide": {"role": "vo_parent", "parent_uuid": "aa" * 12,
                                     "shot_index": 1},
            },
        )
        session.add(seed)
        await session.flush()
        out = await apply_shot_coverage_to_vo_cells(session, project)
        assert out["pipeline"] == 4
        frames = list(
            (await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.number)
            )).scalars().all()
        )
        parents = [f for f in frames if not is_shot_child(f)]
        children = [f for f in frames if is_shot_child(f)]
        assert len(parents) == 2  # две сцены — две ячейки
        assert len(children) == 2  # дочерние кадры на месте
        # Родитель сцены 1 несёт полную лестницу, дочерний — свой кадр.
        p1 = next(f for f in parents if "улица" in str(
            planned_shots_from_attrs(f)[0].get("место")))
        assert [s["id"] for s in planned_shots_from_attrs(p1)] == ["1-K1", "1-K2"]
        assert "1. улица" in str(p1.attrs.get("главное_действие") or "")
        p2 = next(f for f in parents if f is not p1)
        assert [s["id"] for s in planned_shots_from_attrs(p2)] == ["1-K3", "1-K4"]
        assert "2. отделение полиции" in str(p2.attrs.get("главное_действие") or "")
        # Дети ссылаются на родителя сцены и несут свои куски закадра.
        by_child = {f.attrs["camera_subdivide"]["parent_uuid"]: f for f in children}
        assert set(by_child) == {p1.uuid, p2.uuid}
        joined = " ".join(
            (f.voiceover_text or "") for f in frames
        )
        assert " ".join(joined.split()) == " ".join(full.split())
        # Повторный прогон хвоста — без изменений структуры.
        out2 = await apply_shot_coverage_to_vo_cells(session, project)
        assert out2["pipeline"] == 4
        frames2 = list(
            (await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.number)
            )).scalars().all()
        )
        assert len([f for f in frames2 if is_shot_child(f)]) == 2
        assert len([f for f in frames2 if not is_shot_child(f)]) == 2


async def test_group_rerun_drops_stale_ladder_without_second_copy(mem_db) -> None:
    """Повторный fw_shots: не плодить сцену, удалить хвост 1-K7."""
    import uuid as _uuid

    from sqlalchemy import select

    from app.models import Frame, Project, ProjectStatus
    from app.services.vo_shot_expand import (
        apply_shot_coverage_to_vo_cells,
        coverage_shot_id,
        is_shot_child,
        planned_shots_from_attrs,
    )

    full = (
        "Михаил идёт к зданию полиции. Он заходит внутрь. "
        "Он рассказывает дежурному о краже. Он пишет заявление за столом."
    )
    chain = (
        "1. улица — Михаил идёт к зданию полиции\n"
        "(Михаил идёт к зданию полиции. Он заходит внутрь.)\n"
        "2. отделение полиции — пишет заявление за столом\n"
        "(Он рассказывает дежурному о краже. Он пишет заявление за столом.)"
    )
    kadry = [
        {"id": "1-S1-K1", "сцена": 1, "шаблон": "T6", "план": "ОБЩИЙ",
         "ракурс": "фронт", "место": "улица", "действие": "идёт к зданию",
         "parent_id": None},
        {"id": "1-S1-K2", "сцена": 1, "шаблон": "T6", "план": "ОБЩИЙ",
         "ракурс": "фронт", "место": "улица",
         "действие": "подходит к крыльцу", "parent_id": "1-S1-K1"},
        {"id": "1-S2-K1", "сцена": 2, "шаблон": "T5", "план": "СРЕДНИЙ",
         "ракурс": "3/4", "место": "отделение полиции",
         "действие": "сидит, пишет", "parent_id": None},
        {"id": "1-S2-K2", "сцена": 2, "шаблон": "T5", "план": "ДЕТАЛЬ",
         "ракурс": "макро", "место": "отделение полиции",
         "действие": "рука выводит строки", "parent_id": "1-S2-K1"},
    ]
    canvas = {
        "canvas_graph": {
            "nodes": [{
                "id": "n_excel_gpt_fw_shots",
                "data": {"groupId": "script_frames_qc"},
            }],
            "edges": [],
        }
    }
    async with mem_db() as session:
        project = Project(
            slug=f"stale-lad-{_uuid.uuid4().hex[:8]}",
            topic="t",
            status=ProjectStatus.new,
            meta=canvas,
        )
        session.add(project)
        await session.flush()
        seed = Frame(
            project_id=project.id,
            number=1,
            sort_key=1.0,
            uuid="aa" * 12,
            voiceover_text=full,
            duration_seconds=12.0,
            attrs={
                "кадры": [dict(s) for s in kadry],
                "vo_cell_full": full,
                "главное_действие": chain,
                "биты": [{"порядок": 1}],
                "camera_subdivide": {
                    "role": "vo_parent",
                    "parent_uuid": "aa" * 12,
                    "shot_index": 1,
                },
            },
        )
        session.add(seed)
        await session.flush()
        first = await apply_shot_coverage_to_vo_cells(session, project)
        assert first["pipeline"] == 4
        frames = list(
            (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project.id)
                    .order_by(Frame.sort_key, Frame.number)
                )
            ).scalars().all()
        )
        p1 = next(
            f for f in frames
            if not is_shot_child(f)
            and any(s.get("id") == "1-S1-K1" for s in planned_shots_from_attrs(f))
        )
        from sqlalchemy.orm.attributes import flag_modified

        p1.attrs = {
            **dict(p1.attrs or {}),
            "кадры": [dict(s) for s in kadry],
        }
        flag_modified(p1, "attrs")
        await session.flush()
        stale_uid = "ee" * 12
        stale = Frame(
            project_id=project.id,
            number=90,
            sort_key=900.0,
            uuid=stale_uid,
            voiceover_text="Он рассказывает дежурному о краже.",
            duration_seconds=4.0,
            attrs={
                "кадры": [
                    {"id": "1-K7", "сцена": 2, "шаблон": "T5",
                     "parent_id": "1-K4", "закадр": "Он рассказывает дежурному о краже."},
                    {"id": "1-K8", "сцена": 2, "шаблон": "T5",
                     "parent_id": "1-K7", "закадр": "Он пишет заявление за столом."},
                ],
                "vo_cell_full": (
                    "Он рассказывает дежурному о краже. "
                    "Он пишет заявление за столом."
                ),
                "главное_действие": (
                    "2. отделение полиции — пишет заявление за столом\n"
                    "(Он рассказывает дежурному о краже. "
                    "Он пишет заявление за столом.)"
                ),
                "camera_subdivide": {
                    "role": "vo_parent",
                    "parent_uuid": stale_uid,
                    "shot_index": 1,
                    "shots_in_beat": 2,
                    "scene_split": 1,
                    "сцена": 2,
                    "shot_id": "1-K7",
                },
            },
        )
        session.add(stale)
        await session.flush()
        stale_c = Frame(
            project_id=project.id,
            number=91,
            sort_key=910.0,
            uuid="ff" * 12,
            voiceover_text="Он пишет заявление за столом.",
            duration_seconds=4.0,
            attrs={
                "кадры": [{"id": "1-K8", "сцена": 2, "шаблон": "T5"}],
                "vo_cell_full": "Он пишет заявление за столом.",
                "camera_subdivide": {
                    "role": "shot",
                    "parent_uuid": stale_uid,
                    "shot_index": 2,
                    "shots_in_beat": 2,
                    "scene_split": 1,
                    "сцена": 2,
                    "shot_id": "1-K8",
                },
            },
        )
        session.add(stale_c)
        await session.flush()
        second = await apply_shot_coverage_to_vo_cells(session, project)
        assert second["pipeline"] == 4
        frames2 = list(
            (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project.id)
                    .order_by(Frame.sort_key, Frame.number)
                )
            ).scalars().all()
        )
        ids = [coverage_shot_id(f) for f in frames2]
        assert "1-K7" not in ids
        assert "1-K8" not in ids
        assert ids.count("1-S2-K1") == 1
        assert len([f for f in frames2 if is_shot_child(f)]) == 2
        assert len([f for f in frames2 if not is_shot_child(f)]) == 2


def _kadry_frame(number: int, shot_id: str, **extra):
    from types import SimpleNamespace

    return SimpleNamespace(
        number=number,
        uuid=f"u{number:02d}" * 8,
        image_prompt=extra.pop("image_prompt", ""),
        attrs={
            "кадры": [{"id": shot_id, "порядок": 1}],
            "place": extra.get("place", "канцелярия"),
            "shot01_bg": extra.get("shot01_bg", "стол и шкаф"),
            "camera_subdivide": {"role": "vo_parent", "shot_id": shot_id},
        },
    )


def test_coverage_child_detects_flattened_k2() -> None:
    from app.services.vo_shot_expand import (
        coverage_parent_shot_id,
        find_coverage_parent_frame,
        is_coverage_child,
        merge_parent_scene_refs,
        with_parent_scene_lock,
    )

    k1 = _kadry_frame(1, "1-K1")
    k2 = _kadry_frame(2, "1-K2")
    k3 = _kadry_frame(8, "4-K3")
    k1b = _kadry_frame(6, "4-K1")
    assert is_coverage_child(k2)
    assert is_coverage_child(k3)
    assert not is_coverage_child(k1)
    assert coverage_parent_shot_id(k2) == "1-K1"
    assert coverage_parent_shot_id(k3) == "4-K1"
    assert find_coverage_parent_frame([k1, k2], k2) is k1
    assert find_coverage_parent_frame([k1b, k3], k3) is k1b
    refs = merge_parent_scene_refs("parent.png", ["c01.png", "c02.png"], max_refs=2)
    assert refs == ["parent.png", "c01.png"]
    locked = with_parent_scene_lock("сцена", has_parent_ref=True, has_char_ref=True)
    assert locked.startswith("Image 1 is the previous coverage still")
    assert "Image 2 is the character sheet" in locked
    assert "same body count" in locked or "do not invent extra" in locked
    assert "painting already in Image 1" in locked
    assert locked.endswith("сцена")


def test_character_sheet_lock_maps_two_images() -> None:
    from app.services.vo_shot_expand import with_character_sheet_lock

    locked = with_character_sheet_lock("сцена", ["c02", "c03"])
    assert locked.startswith("Image 1 is the identity reference of c02")
    assert "Image 2 is the identity reference of c03" in locked
    assert "TWO different people" in locked
    assert locked.endswith("сцена")
    again = with_character_sheet_lock(locked, ["c02", "c03"])
    assert again == locked


def test_character_sheet_lock_skips_when_parent_lock_present() -> None:
    from app.services.vo_shot_expand import with_character_sheet_lock

    raw = "Image 1 is the previous coverage still of the SAME scene\n\nсцена"
    assert with_character_sheet_lock(raw, ["c02"]) == raw


def test_shot_child_locks_to_vo_parent_not_cross_scene_id() -> None:
    """K2/K3 ячейки 5-* не берут PNG чужого 2-K1 из coverage_parent_id."""
    from types import SimpleNamespace

    from app.services.vo_shot_expand import find_coverage_parent_frame

    k1 = SimpleNamespace(
        number=13,
        uuid="parent-5k1",
        attrs={
            "кадры": [{"id": "5-K1"}],
            "camera_subdivide": {
                "role": "vo_parent",
                "shot_id": "5-K1",
                "parent_uuid": "parent-5k1",
            },
        },
    )
    wrong = SimpleNamespace(
        number=4,
        uuid="other-2k1",
        attrs={
            "кадры": [{"id": "2-K1"}],
            "camera_subdivide": {"role": "vo_parent", "shot_id": "2-K1"},
        },
    )
    k4 = SimpleNamespace(
        number=16,
        uuid="child-5k4",
        attrs={
            "кадры": [{"id": "5-K4", "parent_id": "2-K1"}],
            "camera_subdivide": {
                "role": "shot",
                "shot_id": "5-K4",
                "parent_uuid": "parent-5k1",
                "coverage_parent_id": "2-K1",
                "scene_split": 1,
            },
        },
    )
    assert find_coverage_parent_frame([wrong, k1, k4], k4) is k1


def test_vo_parent_with_x1_parent_id_is_not_shot_child() -> None:
    """K1 новой ячейки с coverage_parent_id — не дочка: PNG-lock не вешаем."""
    from types import SimpleNamespace

    from app.services.vo_shot_expand import is_coverage_child, is_shot_child

    k1 = SimpleNamespace(
        number=13,
        uuid="u13",
        attrs={
            "кадры": [{"id": "5-K1", "parent_id": "2-K1"}],
            "camera_subdivide": {
                "role": "vo_parent",
                "shot_id": "5-K1",
                "coverage_parent_id": "2-K1",
                "scene_split": 1,
            },
        },
    )
    assert is_coverage_child(k1) is True
    assert is_shot_child(k1) is False


def test_merge_flattened_coverage_rebuilds_kadry() -> None:
    from app.services.vo_shot_expand import (
        flattened_coverage_groups,
        merge_flattened_coverage_members,
    )

    k1 = _kadry_frame(1, "5-K1")
    k1.voiceover_text = "двор целиком, героиня у крыльца."
    k1.attrs["кадры"][0]["место"] = "двор усадьбы"
    k1.attrs["кадры"][0]["действие"] = "героиня стоит у крыльца"
    k2 = _kadry_frame(2, "5-K2")
    k2.voiceover_text = "жест рук, две бумаги жалоб."
    k2.attrs["кадры"][0]["место"] = "музейный зал"
    k2.attrs["кадры"][0]["действие"] = "посетитель у портрета"
    k2.attrs["camera_subdivide"]["parent_uuid"] = k2.uuid
    k1.attrs["camera_subdivide"]["parent_uuid"] = k1.uuid
    groups = flattened_coverage_groups([k1, k2])
    assert list(groups) == ["5"]
    parent, extra = merge_flattened_coverage_members(groups["5"])
    assert parent is k1
    assert extra == [k2]
    kadry = parent.attrs["кадры"]
    assert len(kadry) == 2
    assert kadry[0]["id"] == "5-K1"
    assert kadry[0]["parent_id"] is None
    assert kadry[1]["id"] == "5-K2"
    assert kadry[1]["parent_id"] == "5-K1"
    assert "двор целиком" in parent.voiceover_text
    assert "жест рук" in parent.voiceover_text
    assert kadry[1]["закадр"] == "жест рук, две бумаги жалоб."
