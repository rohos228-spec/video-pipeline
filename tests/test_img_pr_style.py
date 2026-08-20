"""img_pr: канонический STYLE одинаковый во всех батчах."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.img_pr_style import (
    STYLE_TAIL,
    wrap_ops_styles,
    wrap_scene_with_style,
)

SCENE = "Background: metro car. Action: reading."


def test_wrap_pins_canonical_noir() -> None:
    short = SCENE + "\nFinal style lock: unified poster, no photorealism.\nNegative: 3D."
    wrapped = wrap_scene_with_style(short, style_id="noir")
    assert SCENE in wrapped
    assert "unified poster, no photorealism" not in wrapped
    assert STYLE_TAIL in wrapped
    again = wrap_scene_with_style(wrapped, style_id="noir")
    assert again == wrapped


def test_wrap_ops_styles_pins_every_op() -> None:
    ops = [
        {
            "frame_uuid": "a",
            "fields": {
                "промт_картинки": SCENE + "\nFinal style lock: short.\nNegative: x."
            },
        }
    ]
    out = wrap_ops_styles(ops, style_id="noir")
    body = out[0]["fields"]["промт_картинки"]
    assert SCENE in body
    assert STYLE_TAIL in body


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
