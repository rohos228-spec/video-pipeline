"""Node write contract: field allowlist + N/N coverage."""

from __future__ import annotations

from app.services.node_write_contract import (
    coverage_report,
    filter_ops_for_node,
    skip_frame_coverage,
)


def test_excel_gpt_prompts_keeps_image_and_anim():
    ops = [
        {
            "frame_uuid": "aa",
            "fields": {
                "промт_картинки": "YES",
                "промт_видео": "MOVE",
                "действие": "открывает папку",
                "место": "кухня",
                "закадр": "НЕЛЬЗЯ",
                "voiceover_text": "НЕЛЬЗЯ",
            },
        }
    ]
    out = filter_ops_for_node(ops, node_kind="excel_gpt_prompts")
    assert out[0]["fields"]["промт_картинки"] == "YES"
    assert out[0]["fields"]["промт_видео"] == "MOVE"
    assert out[0]["fields"]["действие"] == "открывает папку"
    assert "место" not in out[0]["fields"]
    assert "закадр" not in out[0]["fields"]
    assert "voiceover_text" not in out[0]["fields"]


def test_excel_gpt_prompts_keeps_camera_menu_fields():
    ops = [
        {
            "frame_uuid": "aa",
            "fields": {
                "крупность": "Средний план",
                "движение": "наезд",
                "набор": "SET_08",
                "закадр": "НЕЛЬЗЯ",
            },
        }
    ]
    out = filter_ops_for_node(ops, node_kind="excel_gpt_prompts")
    assert out[0]["fields"]["крупность"] == "Средний план"
    assert out[0]["fields"]["движение"] == "наезд"
    assert out[0]["fields"]["набор"] == "SET_08"
    assert "закадр" not in out[0]["fields"]


def test_excel_gpt_strips_prompt_fields():
    ops = [
        {
            "frame_uuid": "aa",
            "fields": {
                "место": "кухня",
                "промт_картинки": "NO",
                "промт_видео": "NO",
            },
        }
    ]
    out = filter_ops_for_node(ops, node_kind="excel_gpt_no_prompts")
    assert "image_prompt" not in out[0]["fields"]
    assert "промт_картинки" not in out[0]["fields"]
    assert out[0]["fields"]["место"] == "кухня"


def test_excel_gpt_strips_prompt_alias_promt_kartinki():
    ops = [{"frame_uuid": "aa", "fields": {"промт_картинки": "NO", "place": "двор"}}]
    out = filter_ops_for_node(ops, node_kind="excel_gpt_no_prompts")
    assert "промт_картинки" not in out[0]["fields"]
    assert out[0]["fields"]["place"] == "двор"
    assert ops[0]["fields"]["промт_картинки"] == "NO"


def test_coverage_fails_if_one_missing():
    r = coverage_report(
        [{"frame_uuid": "a", "fields": {"x": "1"}}],
        expected_uuids=["a", "b"],
    )
    assert r.ok is False and r.missing == ["b"]


def test_empty_ops_not_ok():
    r = coverage_report([], expected_uuids=["a"])
    assert r.ok is False


def test_coverage_ok_when_all_present_even_with_extra():
    r = coverage_report(
        [
            {"frame_uuid": "a", "fields": {"x": "1"}},
            {"frame_uuid": "b", "fields": {"x": "2"}},
            {"frame_uuid": "z", "fields": {"x": "3"}},
        ],
        expected_uuids=["a", "b"],
    )
    assert r.ok is True
    assert r.missing == []
    assert r.extra == ["z"]
    assert r.matched == ["a", "b"]


def test_img_pr_keeps_image_prompt_and_characters():
    ops = [
        {
            "frame_uuid": "aa",
            "fields": {
                "промт_картинки": "YES",
                "персонажи": "c01",
                "промт_видео": "NO",
                "место": "кухня",
            },
        }
    ]
    out = filter_ops_for_node(ops, node_kind="img_pr")
    assert out[0]["fields"]["промт_картинки"] == "YES"
    assert out[0]["fields"]["персонажи"] == "c01"
    assert "промт_видео" not in out[0]["fields"]
    assert "место" not in out[0]["fields"]


def test_skip_frame_coverage_scene_grammar_chars_no_ops():
    assert skip_frame_coverage(
        scene_grammar=True,
        character_registry=False,
        ops_list=[],
        chars_list=[{"id": "c01"}],
        scenes_list=[],
    )


def test_skip_frame_coverage_scene_grammar_scenes_no_ops():
    assert skip_frame_coverage(
        scene_grammar=True,
        character_registry=False,
        ops_list=[],
        chars_list=[],
        scenes_list=[{"id": "s01"}],
    )


def test_skip_frame_coverage_scene_grammar_with_ops_requires_coverage():
    assert not skip_frame_coverage(
        scene_grammar=True,
        character_registry=False,
        ops_list=[{"frame_uuid": "a", "fields": {"x": "1"}}],
        chars_list=[{"id": "c01"}],
        scenes_list=[],
    )


def test_skip_frame_coverage_character_registry_always():
    assert skip_frame_coverage(
        scene_grammar=False,
        character_registry=True,
        ops_list=[],
        chars_list=[{"id": "c01"}],
        scenes_list=[],
    )
    assert skip_frame_coverage(
        scene_grammar=False,
        character_registry=True,
        ops_list=[{"frame_uuid": "a", "fields": {"персонажи": "c01"}}],
        chars_list=[],
        scenes_list=[],
    )


def test_skip_frame_coverage_normal_excel_gpt_never():
    assert not skip_frame_coverage(
        scene_grammar=False,
        character_registry=False,
        ops_list=[{"frame_uuid": "a", "fields": {"x": "1"}}],
        chars_list=[],
        scenes_list=[],
    )
    assert not skip_frame_coverage(
        scene_grammar=False,
        character_registry=False,
        ops_list=[],
        chars_list=[{"id": "c01"}],
        scenes_list=[],
    )


def test_anim_pr_keeps_only_animation_prompt():
    ops = [
        {
            "frame_uuid": "aa",
            "fields": {
                "промт_видео": "MOVE",
                "промт_картинки": "NO",
                "персонажи": "c01",
            },
        }
    ]
    out = filter_ops_for_node(ops, node_kind="anim_pr")
    assert out[0]["fields"]["промт_видео"] == "MOVE"
    assert "промт_картинки" not in out[0]["fields"]
    assert "персонажи" not in out[0]["fields"]


def test_excel_gpt_drops_replace_frames() -> None:
    ops = [
        {"target": "replace_frames", "frames": [{"закадр": "нет"}]},
        {"frame_uuid": "aa", "fields": {"закадр": "да", "место": "двор"}},
    ]
    out = filter_ops_for_node(ops, node_kind="excel_gpt_no_prompts")
    assert len(out) == 1
    assert out[0]["frame_uuid"] == "aa"
    assert "закадр" not in out[0]["fields"]
    assert out[0]["fields"]["место"] == "двор"


def test_excel_gpt_no_prompts_strips_voiceover() -> None:
    ops = [
        {
            "frame_uuid": "aa",
            "fields": {
                "закадр": "НЕЛЬЗЯ",
                "voiceover_text": "НЕЛЬЗЯ",
                "смысл": "НЕЛЬЗЯ",
                "место": "двор",
            },
        }
    ]
    out = filter_ops_for_node(ops, node_kind="excel_gpt_no_prompts")
    assert out[0]["fields"]["место"] == "двор"
    assert "закадр" not in out[0]["fields"]
    assert "voiceover_text" not in out[0]["fields"]
    assert "смысл" not in out[0]["fields"]
