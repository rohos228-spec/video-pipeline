"""Read-only inherited background metadata on the montage board."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.montage_board import _scene_background_for_board


@pytest.fixture
def scene(tmp_path, monkeypatch):
    from app.services import scan_frames

    monkeypatch.setattr(scan_frames, "is_valid_scene_image", lambda p: p.is_file())
    bg = tmp_path / "backgrounds" / "scene.png"
    bg.parent.mkdir()
    bg.write_bytes(b"mock background")
    parent = SimpleNamespace(number=1, uuid="parent", attrs={
        "scene_background_ref": {"status": "ready", "path": "backgrounds/scene.png", "hash": "bg-v2"}
    })
    child = SimpleNamespace(number=2, uuid="child", attrs={
        "camera_subdivide": {"role": "shot", "parent_uuid": "parent"},
        "scene_background_used_hash": "bg-v2",
    })
    return SimpleNamespace(root=tmp_path, bg=bg, parent=parent, child=child,
                           frames=[parent, child])


def test_child_inherits_scene_background_preview(scene):
    s = scene
    dto = _scene_background_for_board(s.root, s.frames, s.child, enabled=True, has_image=True)
    assert dto["parent_number"] == 1
    assert dto["status"] == "ready"
    assert dto["path"] == "backgrounds/scene.png"
    assert dto["hash"] == "bg-v2"
    assert dto["image_url"]
    assert not dto["stale"]
    assert not dto["error"]


@pytest.mark.parametrize("used", [None, "bg-v1"])
def test_existing_frame_with_old_or_missing_hash_is_stale(scene, used):
    s = scene
    s.child.attrs["scene_background_used_hash"] = used
    dto = _scene_background_for_board(s.root, s.frames, s.child, enabled=True, has_image=True)
    assert dto["stale"]
    assert dto["image_url"]


def test_explicit_stale_marker_is_shown_without_image(scene):
    s = scene
    s.child.attrs["scene_background_stale"] = True
    dto = _scene_background_for_board(s.root, s.frames, s.child, enabled=True)
    assert dto["stale"]


def test_not_yet_generated_frame_is_not_stale_by_missing_used_hash(scene):
    s = scene
    dto = _scene_background_for_board(s.root, s.frames, s.parent, enabled=True)
    assert not dto["stale"]


@pytest.mark.parametrize("path", ["../outside.png", "characters/identity.png", "backgrounds/nested/scene.png"])
def test_unsafe_background_path_is_not_exposed(scene, path):
    s = scene
    s.parent.attrs["scene_background_ref"]["path"] = path
    dto = _scene_background_for_board(s.root, s.frames, s.child, enabled=True)
    assert dto["path"] is None
    assert dto["image_url"] is None
    assert dto["error"]


def test_missing_background_does_not_break_board(scene):
    s = scene
    s.parent.attrs = {}
    dto = _scene_background_for_board(s.root, s.frames, s.child, enabled=True)
    assert dto["status"] == "missing"
    assert dto["image_url"] is None
    assert dto["error"]


def test_broken_parent_does_not_break_board(scene):
    s = scene
    dto = _scene_background_for_board(s.root, [s.child], s.child, enabled=True)
    assert dto["image_url"] is None
    assert dto["error"]


def test_old_group_does_not_resolve_backgrounds(scene, monkeypatch):
    from app.services import scene_background_refs

    resolver = Mock(side_effect=AssertionError("must not inspect backgrounds"))
    monkeypatch.setattr(scene_background_refs, "scene_parent", resolver)
    s = scene
    assert _scene_background_for_board(s.root, s.frames, s.child, enabled=False) is None
    resolver.assert_not_called()


def test_scene_background_ui_is_read_only_and_opt_in():
    root = Path(__file__).resolve().parents[1]
    source = (root / "web/src/components/canvas/montage-frame-refs.tsx").read_text(encoding="utf-8")
    assert 'source: "scene_background"' in source
    background_block = source.split('key: "scene-background"', 1)[1].split("});", 1)[0]
    assert 'title: "Фон сцены"' in background_block
    assert "canDelete: false" in background_block
    assert 'if (source === "scene_background") return;' in source
    assert "frame.cinema_backgrounds && !frame.scene_background_ref?.image_url" in source
    assert "Запустите Фон сцены" in source
