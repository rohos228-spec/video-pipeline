"""Cinema background attachment integration, with no provider/API calls."""
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import Frame, FrameStatus, HITLDecision, Project, ProjectStatus
from app.orchestrator.steps import generate_images as images
from app.services import montage_board_regen as regen


@pytest.fixture
def cinema(tmp_path, monkeypatch):
    from app.services import node_groups

    monkeypatch.setattr("app.settings.settings.data_dir", str(tmp_path))
    monkeypatch.setattr(node_groups, "canvas_has_cinema_backgrounds", lambda p: True, raising=False)
    project = Project(id=47, slug="cinema", topic="test", meta={})
    frame = Frame(id=1, project_id=47, number=1, attrs={"characters": "c01"},
                  image_prompt="A scene", status=FrameStatus.image_prompt_ready)
    background = project.data_dir / "backgrounds" / "scene.png"
    identity = project.data_dir / "characters" / "c01.png"
    parent = project.data_dir / "scenes" / "frame_001_parent.png"
    for path in (background, identity, parent):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"mock")
    service = ModuleType("app.services.scene_background_refs")
    service.background_ref_for_frame = AsyncMock(return_value=background)
    service.ensure_backgrounds_ready = AsyncMock()
    service.mark_background_used = AsyncMock()

    def merge(bg, refs, *, max_refs=2):
        result = list(dict.fromkeys([bg, *refs]))
        if len(result) > max_refs:
            raise RuntimeError("background refs overflow: max 2")
        return result

    service.merge_background_refs = MagicMock(side_effect=merge)
    monkeypatch.setitem(sys.modules, service.__name__, service)
    monkeypatch.setattr(images, "_load_refs_for_frame", AsyncMock(return_value=[identity]))
    monkeypatch.setattr(images, "_coverage_parent_png", AsyncMock(return_value=parent))
    session = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.get = AsyncMock(return_value=project)
    session.refresh = AsyncMock()
    return SimpleNamespace(project=project, frame=frame, bg=background, identity=identity,
                           parent=parent, service=service, session=session)


@pytest.mark.asyncio
async def test_root_has_background_then_identity(cinema):
    c = cinema
    refs, child = await images._cinema_background_refs(c.session, c.project, c.frame)
    assert refs == [c.bg, c.identity]
    assert not child
    c.service.background_ref_for_frame.assert_awaited_once_with(c.session, c.project, c.frame)


@pytest.mark.asyncio
async def test_two_identities_are_not_silently_truncated(cinema, monkeypatch):
    c = cinema
    monkeypatch.setattr(images, "_load_refs_for_frame", AsyncMock(return_value=[c.identity, Path("c02.png")]))
    with pytest.raises(RuntimeError, match="overflow"):
        await images._cinema_background_refs(c.session, c.project, c.frame)


@pytest.mark.asyncio
async def test_child_uses_parent_and_background_not_identities(cinema, monkeypatch):
    from app.services import vo_shot_expand

    c = cinema
    monkeypatch.setattr(vo_shot_expand, "is_shot_child", lambda f: True)
    refs, child = await images._cinema_background_refs(c.session, c.project, c.frame)
    assert refs == [c.bg, c.parent]
    assert child
    images._load_refs_for_frame.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_parent_fails(cinema, monkeypatch):
    from app.services import vo_shot_expand

    c = cinema
    monkeypatch.setattr(vo_shot_expand, "is_shot_child", lambda f: True)
    monkeypatch.setattr(images, "_coverage_parent_png", AsyncMock(return_value=None))
    with pytest.raises(RuntimeError, match="нет still родителя"):
        await images._cinema_background_refs(c.session, c.project, c.frame)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", ["missing background", "stale background"])
async def test_missing_or_stale_background_fails(cinema, error):
    c = cinema
    c.service.background_ref_for_frame.side_effect = RuntimeError(error)
    with pytest.raises(RuntimeError, match=error):
        await images._cinema_background_refs(c.session, c.project, c.frame)


@pytest.mark.asyncio
async def test_preflight_stops_before_generation(cinema, monkeypatch):
    from app.services import anim_pr_sidecar, artifact_recovery

    c = cinema
    c.project.status = ProjectStatus.generating_images
    monkeypatch.setattr(anim_pr_sidecar, "ensure_anim_pr_sidecar", lambda *_: None)
    monkeypatch.setattr(artifact_recovery, "recover_scene_images_from_disk", AsyncMock(return_value=[]))
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = [c.frame]
    c.session.execute = AsyncMock(return_value=rows)
    c.service.ensure_backgrounds_ready.side_effect = RuntimeError("missing background")
    provider = AsyncMock()
    monkeypatch.setattr(images, "generate_image_with_retries", provider)
    with pytest.raises(RuntimeError, match="missing background"):
        await images.run(c.session, c.project, MagicMock())
    c.service.ensure_backgrounds_ready.assert_awaited_once_with(c.session, c.project, [c.frame])
    provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipeline_fresh_refs_bypass_regen_button(cinema, monkeypatch):
    from app.services import image_ref_lock, img_pr_style

    c = cinema
    result = MagicMock()
    result.scalar_one_or_none.return_value = SimpleNamespace(decision=HITLDecision.regenerate)
    result.scalars.return_value.all.return_value = []
    c.session.execute = AsyncMock(return_value=result)
    monkeypatch.setattr(images, "_sheet_for_project", lambda p: MagicMock())
    monkeypatch.setattr(img_pr_style, "ensure_style_lock", lambda p, *_: p)
    lock = MagicMock(side_effect=lambda prompt, refs, **kw: ("ROLE LOCK " + prompt, refs))
    monkeypatch.setattr(image_ref_lock, "prepare_refs_and_prompt", lock)
    provider = AsyncMock(return_value=SimpleNamespace(file_path=Path("generated.png"), raw_url=""))
    monkeypatch.setattr(images, "generate_image_with_retries", provider)
    monkeypatch.setattr(images, "send_hitl_photo", AsyncMock())
    outsee = MagicMock(regenerate_image=AsyncMock())
    await images._generate_and_send(c.session, MagicMock(), outsee, MagicMock(), c.project,
                                    c.frame, c.project.data_dir / "scenes")
    outsee.regenerate_image.assert_not_awaited()
    assert provider.await_args.kwargs["reference_image"] == [c.bg, c.identity]
    assert provider.await_args.kwargs["prompt"].startswith("ROLE LOCK ")
    assert lock.call_args.args[1] == [c.bg, c.identity]
    c.service.mark_background_used.assert_awaited_once_with(c.session, c.project, c.frame)


@pytest.mark.asyncio
async def test_manual_ref_cannot_evict_identity_or_background(cinema, monkeypatch):
    from app.services import montage_frame_refs

    c = cinema
    monkeypatch.setattr(montage_frame_refs, "manual_ref_paths", lambda *_: [Path("manual.png")])
    with pytest.raises(RuntimeError, match="overflow"):
        await regen._montage_shot1_refs(c.session, c.project, c.frame)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["same_prompt", "edit_prompt"])
async def test_montage_modes_keep_bg_and_identity(cinema, monkeypatch, mode):
    from app.services import image_ref_lock, img_pr_style

    c = cinema
    monkeypatch.setattr(regen, "_frame_by_number", AsyncMock(return_value=c.frame))
    monkeypatch.setattr(regen, "resolve_image_prompt", AsyncMock(return_value="A scene"))
    monkeypatch.setattr(regen, "_persist_image_prompt", AsyncMock())
    monkeypatch.setattr(img_pr_style, "ensure_style_lock", lambda p, *_: p)
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = [c.frame]
    c.session.execute = AsyncMock(return_value=rows)
    lock = MagicMock(side_effect=lambda prompt, refs, **kw: (prompt, refs))
    monkeypatch.setattr(image_ref_lock, "prepare_refs_and_prompt", lock)
    prep = await regen.prepare_image_regen(c.session, c.project, 1, shot=1,
                                           mode=mode, new_prompt="Edited scene")
    assert prep.refs == [c.bg, c.identity]
    assert lock.call_args.args[1] == prep.refs


@pytest.mark.asyncio
async def test_correction_cannot_bypass_required_refs(cinema, monkeypatch):
    c = cinema
    monkeypatch.setattr(regen, "_frame_by_number", AsyncMock(return_value=c.frame))
    monkeypatch.setattr(regen, "find_shot1_image", lambda *_: Path("current.png"))
    with pytest.raises(RuntimeError, match="overflow"):
        await regen.prepare_image_regen(c.session, c.project, 1, shot=1,
                                        mode="correction", correction="Change camera")


@pytest.mark.asyncio
async def test_montage_success_marks_background_used(cinema, monkeypatch):
    c = cinema
    monkeypatch.setattr(regen, "finalize_scene_image", AsyncMock())
    monkeypatch.setattr(regen, "_frame_by_number", AsyncMock(return_value=c.frame))
    prep = regen.ImageRegenPrep(project_id=47, frame_number=1, shot=1,
                               prompt_text="Scene", file_path=Path("new.png"))
    await regen.finalize_image_regen(c.session, c.project, prep, Path("new.png"))
    c.service.mark_background_used.assert_awaited_once_with(c.session, c.project, c.frame)


@pytest.mark.asyncio
async def test_old_group_keeps_manual_ref_priority(cinema, monkeypatch):
    from app.services import montage_frame_refs, node_groups

    c = cinema
    manual = Path("manual.png")
    monkeypatch.setattr(node_groups, "canvas_has_cinema_backgrounds", lambda p: False)
    monkeypatch.setattr(montage_frame_refs, "manual_ref_paths", lambda *_: [manual])
    monkeypatch.setattr(regen, "_load_refs_for_frame", AsyncMock(return_value=[c.identity, Path("c02.png")]))
    refs, child = await regen._montage_shot1_refs(c.session, c.project, c.frame)
    assert refs == [manual, c.identity]
    assert not child
    c.service.background_ref_for_frame.assert_not_awaited()


@pytest.mark.asyncio
async def test_scheduler_rejects_child_with_missing_parent(cinema, monkeypatch):
    from app.services import vo_shot_expand

    c = cinema
    monkeypatch.setattr(vo_shot_expand, "is_shot_child", lambda f: True)
    monkeypatch.setattr(vo_shot_expand, "uses_parent_still", lambda f: True)
    monkeypatch.setattr(vo_shot_expand, "find_coverage_parent_frame", lambda *_: None)
    monkeypatch.setattr(images, "frame_needs_shot1_image", lambda *_: True)
    rows = MagicMock()
    rows.scalars.return_value.all.return_value = [c.frame]
    c.session.execute = AsyncMock(return_value=rows)
    with pytest.raises(RuntimeError, match="нет родителя"):
        await images._claim_shot1_batch(c.session, c.project.id,
                                       c.project.data_dir / "scenes", project=c.project)


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [[], ["c02"]])
async def test_montage_explicit_identity_override_is_preserved(cinema, override):
    c = cinema
    refs, child = await regen._montage_shot1_refs(c.session, c.project, c.frame,
                                                 ref_person_ids=override)
    assert refs == [c.bg, c.identity]
    assert not child
    assert images._load_refs_for_frame.await_args.kwargs["persons_override"] == override


@pytest.mark.asyncio
async def test_montage_missing_bg_fails_even_with_pinned_prompt(cinema, monkeypatch):
    c = cinema
    monkeypatch.setattr(regen, "_frame_by_number", AsyncMock(return_value=c.frame))
    c.service.background_ref_for_frame.side_effect = RuntimeError("missing background")
    with pytest.raises(RuntimeError, match="missing background"):
        await regen.prepare_image_regen(c.session, c.project, 1, shot=1,
                                        pinned_prompt="Operator pinned prompt")
