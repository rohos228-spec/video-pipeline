from app.services.prompt_step_presets import resolve_prompt_preset


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
