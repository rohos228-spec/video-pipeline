"""img_pr: STYLE wrap отключён — сцена уходит как есть."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.img_pr_style import wrap_ops_styles, wrap_scene_with_style

SCENE = "Background: metro car. Action: reading."


def test_wrap_is_noop_regardless_of_style_id() -> None:
    assert wrap_scene_with_style(SCENE) == SCENE
    assert wrap_scene_with_style(SCENE, style_id="knitted") == SCENE
    assert wrap_scene_with_style(SCENE, style_id="noir") == SCENE
    assert (
        wrap_scene_with_style(
            SCENE, style_block="STYLE: custom.\n\nFinal style lock: custom."
        )
        == SCENE
    )
    assert "Archival Noir" not in wrap_scene_with_style(SCENE, style_id="noir")


def test_wrap_ops_styles_is_noop() -> None:
    ops = [{"frame_uuid": "a", "fields": {"промт_картинки": SCENE}}]
    out = wrap_ops_styles(ops, style_id="knitted")
    assert out[0]["fields"]["промт_картинки"] == SCENE


def test_resolve_project_img_style_from_meta() -> None:
    from app.services.img_pr_style import resolve_project_img_style

    project = SimpleNamespace(meta={"img_style": "knitted"})
    assert resolve_project_img_style(project) == "knitted"


def test_resolve_project_img_style_from_variant() -> None:
    from app.services.img_pr_style import resolve_project_img_style

    project = SimpleNamespace(
        meta={"prompt_slot_variants": {"n_image_prompts": {"main": "knitted_v1"}}}
    )
    assert resolve_project_img_style(project) == "knitted"


def test_resolve_project_img_style_none_without_style() -> None:
    from app.services.img_pr_style import resolve_project_img_style

    project = SimpleNamespace(meta={})
    assert resolve_project_img_style(project) is None
