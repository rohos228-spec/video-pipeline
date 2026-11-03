from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.services import scene_background_refs as bg


def frame(uid, number, parent=None):
    attrs = {
        "place": "кабинет",
        "площадка": {"зоны": [{"id": "кабинет", "что": "Белые стены, окно слева.", "предметы": []}], "проходы": [], "люди": [{"кто": "c01"}]},
        "кадры": [{"стиль": "style_noir_watercolour_16_09"}],
    }
    if parent:
        attrs["camera_subdivide"] = {"role": "shot", "parent_uuid": parent}
    return SimpleNamespace(uuid=uid, number=number, attrs=attrs, scene_id=1)


def image(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (512, 512), "gray").save(path, compress_level=0)
    return path


def ready(parent, root):
    prompt, digest = bg.background_spec(parent)
    path = image(root / "backgrounds" / f"scene_{parent.uuid}.png")
    parent.attrs[bg.BACKGROUND_ATTR] = {"status": "ready", "hash": digest, "path": str(path.relative_to(root)), "prompt": prompt}
    return path, digest


def test_logical_scenes_ignore_shared_database_scene_id():
    a, b, c = frame("a", 1), frame("b", 2), frame("c", 3, "a")
    groups = bg.scene_members([a, b, c])
    assert set(groups) == {"a", "b"}
    assert groups["a"][1] == [a, c]
    a.number, c.number = 20, 40
    assert bg.scene_parent([a, b, c], c) is a


def test_missing_or_cyclic_parent_rejected():
    with pytest.raises(RuntimeError, match="родитель"):
        bg.scene_parent([frame("c", 3, "missing")], frame("c", 3, "missing"))
    a, b = frame("a", 1, "b"), frame("b", 2, "a")
    with pytest.raises(RuntimeError, match="зациклена"):
        bg.scene_parent([a, b], a)


def test_prompt_uses_inventory_not_people_and_hash_tracks_changes():
    parent = frame("a", 1)
    prompt, digest = bg.background_spec(parent)
    assert "EMPTY FILM SET" in prompt
    assert "Белые стены" in prompt
    assert '"люди"' not in prompt
    assert "c01" not in prompt
    parent.attrs["площадка"]["люди"] = []
    assert bg.background_spec(parent)[1] == digest
    parent.attrs["площадка"]["зоны"][0]["что"] = "Чёрные стены"
    assert bg.background_spec(parent)[1] != digest


def test_missing_inventory_or_style_fails_before_generation():
    parent = frame("a", 1)
    parent.attrs.pop("площадка")
    with pytest.raises(RuntimeError, match="Площадка"):
        bg.background_spec(parent)
    parent = frame("a", 1)
    parent.attrs["кадры"] = []
    with pytest.raises(RuntimeError, match="Стиль"):
        bg.background_spec(parent)


def test_valid_background_is_current_and_cannot_escape_directory(tmp_path):
    parent = frame("a", 1)
    path, _ = ready(parent, tmp_path)
    assert bg.background_path(tmp_path, parent) == path.resolve()
    parent.attrs[bg.BACKGROUND_ATTR]["path"] = "../outside.png"
    with pytest.raises(RuntimeError, match="файл"):
        bg.background_path(tmp_path, parent)


def test_stale_inventory_rejects_background(tmp_path):
    parent = frame("a", 1)
    ready(parent, tmp_path)
    parent.attrs["place"] = "двор"
    with pytest.raises(RuntimeError, match="устарел"):
        bg.background_path(tmp_path, parent)


def test_merge_no_silent_reference_loss():
    background, identity = Path("background.png"), Path("c01.png")
    assert bg.merge_background_refs(background, [identity, background]) == [background, identity]
    with pytest.raises(RuntimeError, match="не будут отброшены"):
        bg.merge_background_refs(background, [identity, Path("c02.png")])


@pytest.mark.asyncio
async def test_preflight_blocks_old_images_and_marking_recovers(tmp_path, monkeypatch):
    parent, child = frame("a", 1), frame("b", 2, "a")
    ready(parent, tmp_path)
    image(tmp_path / "scenes" / "frame_002_deadbeef.png")
    project = SimpleNamespace(id=1, data_dir=tmp_path)
    session = SimpleNamespace(flush=AsyncMock())
    monkeypatch.setattr(bg, "_frames", AsyncMock(return_value=[parent, child]))
    with pytest.raises(RuntimeError, match="Перегенерируйте"):
        await bg.ensure_backgrounds_ready(session, project, [parent, child])
    await bg.mark_background_used(session, project, child)
    await bg.ensure_backgrounds_ready(session, project, [parent, child])
    assert child.attrs[bg.USED_HASH_ATTR] == parent.attrs[bg.BACKGROUND_ATTR]["hash"]


@pytest.mark.asyncio
async def test_generation_is_idempotent_and_separate_per_scene(tmp_path, monkeypatch):
    from app.orchestrator.steps import generate_images
    from app.services import media_route, outsee_retry, step_cancel, vibecode_catalog

    a, b, child = frame("a", 1), frame("b", 2), frame("c", 3, "a")
    frames = [a, b, child]
    project = SimpleNamespace(id=1, data_dir=tmp_path, image_relax=False)
    session = SimpleNamespace(commit=AsyncMock())
    monkeypatch.setattr(bg, "_frames", AsyncMock(return_value=frames))
    monkeypatch.setattr(step_cancel, "raise_if_cancelled", lambda _id: None)
    monkeypatch.setattr(media_route, "image_provider_for", lambda _model: "grsai")
    monkeypatch.setattr(vibecode_catalog, "resolve_node_media_settings", lambda *a, **k: {
        "image_generator_id": "unused", "aspect_slug": "9:16", "resolution_slug": "2K", "quality_slug": None,
    })
    calls = []

    async def generate(*args, **kwargs):
        calls.append(kwargs)
        image(kwargs["out_path"])

    monkeypatch.setattr(outsee_retry, "generate_image_with_retries", generate)
    report = await bg.generate_scene_backgrounds(session, project)
    assert len(calls) == 2
    assert report["generated"] == ["a", "b"]
    assert all("backgrounds" == c["out_path"].parent.name for c in calls)
    assert not (tmp_path / "scenes").exists()
    again = await bg.generate_scene_backgrounds(session, project)
    assert len(calls) == 2
    assert again["reused"] == ["a", "b"]
    a.attrs["place"] = "новый кабинет"
    await bg.generate_scene_backgrounds(session, project)
    assert len(calls) == 3
    assert child.attrs["scene_background_stale"]


@pytest.mark.asyncio
async def test_failed_generation_persists_failure(tmp_path, monkeypatch):
    from app.services import media_route, outsee_retry, step_cancel, vibecode_catalog

    parent = frame("a", 1)
    monkeypatch.setattr(bg, "_frames", AsyncMock(return_value=[parent]))
    monkeypatch.setattr(step_cancel, "raise_if_cancelled", lambda _id: None)
    monkeypatch.setattr(media_route, "image_provider_for", lambda _model: "grsai")
    monkeypatch.setattr(vibecode_catalog, "resolve_node_media_settings", lambda *a, **k: {
        "image_generator_id": "unused", "aspect_slug": "9:16", "resolution_slug": "2K", "quality_slug": None,
    })
    monkeypatch.setattr(outsee_retry, "generate_image_with_retries", AsyncMock(side_effect=RuntimeError("provider down")))
    with pytest.raises(RuntimeError, match="provider down"):
        await bg.generate_scene_backgrounds(SimpleNamespace(commit=AsyncMock()), SimpleNamespace(id=1, data_dir=tmp_path, image_relax=False))
    assert parent.attrs[bg.BACKGROUND_ATTR]["status"] == "failed"


def test_background_role_locks_set_but_allows_state_changes(tmp_path):
    from app.services.image_ref_lock import classify_attached_ref, prepare_refs_and_prompt

    background = tmp_path / "backgrounds" / "scene.png"
    identity = tmp_path / "characters" / "c01.png"
    prompt, refs = prepare_refs_and_prompt("Door is now open", [background, identity], sheet_ids=["c01"])
    assert classify_attached_ref(background)[0] == "background"
    assert "Image 1 is the EMPTY SET" in prompt
    assert "Image 2 is the identity reference of c01" in prompt
    assert "overrides INITIAL" in prompt
    assert refs == [background, identity]
