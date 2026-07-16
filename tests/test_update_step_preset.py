import json
from pathlib import Path

import pytest

from app.services.prompt_step_presets import resolve_prompt_preset, update_step_preset

from tests.conftest import patch_prompt_roots


def test_resolve_script_default_preset():
    preset = resolve_prompt_preset("script", "default")
    assert preset is not None
    assert preset["id"] == "default"
    assert preset["blocks"]["script_output_contract"] == "voiceover_txt_60s"


def test_resolve_script_long_preset():
    preset = resolve_prompt_preset("script", "zakadrovyuTekst_long_story")
    assert preset is not None
    assert preset["id"] == "long"
    assert preset["blocks"]["script_output_contract"] == "long_cells_txt_10000"
    assert preset["extra_blocks"]["script_segmentation_rules"] == "long_cells_110_140"


def test_resolve_script_cyrillic_alias():
    preset = resolve_prompt_preset("script", "Новый промт 12.05")
    assert preset is not None
    assert preset["id"] == "editor"


def test_update_step_preset_label_and_blocks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _, user = patch_prompt_roots(monkeypatch, tmp_path, folders=())
    presets_dir = user / "step-presets"
    presets_dir.mkdir()
    data = {
        "step_code": "img_pr",
        "presets": {
            "test_preset": {
                "label": "Old",
                "blocks": {"visual_style": "block_a"},
                "omit_slots": [],
            }
        },
    }
    (presets_dir / "img_pr.json").write_text(json.dumps(data), encoding="utf-8")

    updated = update_step_preset(
        "img_pr",
        "test_preset",
        label="New label",
        blocks={"visual_style": "block_b", "lighting": "soft_light"},
    )
    assert updated["label"] == "New label"
    assert updated["blocks"]["visual_style"] == "block_b"
    assert updated["blocks"]["lighting"] == "soft_light"

    reloaded = json.loads((presets_dir / "img_pr.json").read_text(encoding="utf-8"))
    assert reloaded["presets"]["test_preset"]["label"] == "New label"
    assert "visual_style" not in reloaded["presets"]["test_preset"].get("omit_slots", [])


def test_update_step_preset_unomits_assigned_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _, user = patch_prompt_roots(monkeypatch, tmp_path, folders=())
    presets_dir = user / "step-presets"
    presets_dir.mkdir()
    data = {
        "presets": {
            "knitted": {
                "label": "Knitted",
                "blocks": {"composition": "vertical_9_16"},
                "omit_slots": ["world", "visual_style"],
            }
        },
    }
    (presets_dir / "img_pr.json").write_text(json.dumps(data), encoding="utf-8")

    update_step_preset("img_pr", "knitted", blocks={"visual_style": "textile_style"})
    reloaded = json.loads((presets_dir / "img_pr.json").read_text(encoding="utf-8"))
    preset = reloaded["presets"]["knitted"]
    assert preset["blocks"]["visual_style"] == "textile_style"
    assert "visual_style" not in preset["omit_slots"]
