"""img_pr STYLE-lock from project, not hardcoded Archival Noir."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.services.img_pr_style import wrap_ops_styles, wrap_scene_with_style

SCENE = "Background: metro car. Action: reading."


def test_wrap_no_style_does_not_inject_archival_noir() -> None:
    wrapped = wrap_scene_with_style(SCENE)
    assert wrapped == SCENE
    assert "Archival Noir" not in wrapped


def test_wrap_no_style_warns_once() -> None:
    import app.services.img_pr_style as mod

    if hasattr(mod, "_NO_STYLE_WARNED"):
        mod._NO_STYLE_WARNED = False
    logger = getattr(mod, "logger", None)
    assert logger is not None, "must log a warning when no style is configured"
    with patch.object(logger, "warning") as warn:
        wrap_scene_with_style("Scene A")
        wrap_scene_with_style("Scene B")
    assert warn.call_count == 1


def test_wrap_knitted_has_textile_markers_not_noir() -> None:
    wrapped = wrap_scene_with_style(SCENE, style_id="knitted")
    low = wrapped.casefold()
    assert "Archival Noir" not in wrapped
    assert SCENE in wrapped
    assert any(m in low for m in ("knitted", "textile", "вязан", "felt"))
    assert "Final style lock" in wrapped or "STYLE:" in wrapped


def test_wrap_textile_and_cyrillic_alias_is_knitted() -> None:
    for sid in ("textile", "вязаный"):
        wrapped = wrap_scene_with_style(SCENE, style_id=sid)
        assert "Archival Noir" not in wrapped
        low = wrapped.casefold()
        assert any(m in low for m in ("knitted", "textile", "вязан", "felt"))


def test_wrap_explicit_noir_still_works() -> None:
    wrapped = wrap_scene_with_style(SCENE, style_id="noir")
    assert "Archival Noir Watercolor" in wrapped
    assert SCENE in wrapped
    assert "Final style lock" in wrapped


def test_wrap_style_block_uses_provided_block() -> None:
    block = "STYLE: custom lock. Do this.\n\nFinal style lock: custom lock."
    wrapped = wrap_scene_with_style(SCENE, style_block=block)
    assert "Archival Noir" not in wrapped
    assert "custom lock" in wrapped
    assert SCENE in wrapped


def test_wrap_ops_styles_passes_style_id() -> None:
    ops = [{"frame_uuid": "a", "fields": {"промт_картинки": SCENE}}]
    out = wrap_ops_styles(ops, style_id="knitted")
    body = out[0]["fields"]["промт_картинки"]
    assert "Archival Noir" not in body
    assert SCENE in body
    assert any(m in body.casefold() for m in ("knitted", "textile", "felt"))


def test_clay_body_not_wrapped_even_with_noir_id() -> None:
    body = (
        "Minimalist Claymation Plasticine 2D-Look Miniature Illustration. "
        "A clay person sews in a workshop."
    )
    wrapped = wrap_scene_with_style(body, style_id="noir")
    assert wrapped == body
    assert "Archival Noir" not in wrapped


def test_resolve_project_img_style_from_meta() -> None:
    from app.services.img_pr_style import resolve_project_img_style

    project = SimpleNamespace(meta={"img_style": "knitted"})
    assert resolve_project_img_style(project) == "knitted"


def test_resolve_project_img_style_from_variant() -> None:
    from app.services.img_pr_style import resolve_project_img_style

    project = SimpleNamespace(meta={})
    assert resolve_project_img_style(project, variant="img_pr_knitted") == "knitted"


def test_resolve_project_img_style_empty() -> None:
    from app.services.img_pr_style import resolve_project_img_style

    project = SimpleNamespace(meta={})
    assert resolve_project_img_style(project) is None


def test_resolve_project_img_style_from_prompt_overrides() -> None:
    from app.services.img_pr_style import resolve_project_img_style

    project = SimpleNamespace(
        meta={}, prompt_overrides={"img_pr": "knitted_v2"}
    )
    assert resolve_project_img_style(project) == "knitted"
