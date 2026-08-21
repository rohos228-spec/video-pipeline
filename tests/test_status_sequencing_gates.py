"""Регрессии: порядок media-нод, video data-guard, sidecar NodeRun, prepare atomic."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Artifact,
    ArtifactKind,
    Base,
    Frame,
    NodeRun,
    NodeRunStatus,
    Project,
    ProjectStatus,
    Workflow,
    WorkflowRun,
    WorkflowRunStatus,
)
from app.orchestrator.auto_advance import TRANSITIONS, _apply_approve
from app.services.step_data_guard import can_enter_running


@pytest.fixture
async def session(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _media_graph_meta() -> dict:
    """Canvas: images → videos (BFS прыгнул бы в video), плюс anim_pr на графе."""
    return {
        "canvas_graph": {
            "nodes": [
                {
                    "id": "n_images",
                    "type": "images",
                    "position": {"x": 0, "y": 0},
                    "data": {},
                },
                {
                    "id": "n_anim",
                    "type": "animation_prompts",
                    "position": {"x": 150, "y": 0},
                    "data": {},
                },
                {
                    "id": "n_videos",
                    "type": "videos",
                    "position": {"x": 300, "y": 0},
                    "data": {},
                },
            ],
            # Ребро images→videos провоцирует BFS-прыжок мимо anim_pr.
            "edges": [
                {
                    "id": "e_iv",
                    "source": "n_images",
                    "target": "n_videos",
                    "sourceHandle": "out",
                    "targetHandle": "in",
                },
                {
                    "id": "e_ia",
                    "source": "n_images",
                    "target": "n_anim",
                    "sourceHandle": "out",
                    "targetHandle": "in",
                },
                {
                    "id": "e_av",
                    "source": "n_anim",
                    "target": "n_videos",
                    "sourceHandle": "out",
                    "targetHandle": "in",
                },
            ],
        }
    }


@pytest.mark.asyncio
async def test_images_ready_auto_advance_prefers_anim_pr_not_videos(session) -> None:
    """images_ready → generating_animation_prompts даже если graph ведёт в videos."""
    p = Project(
        slug="seq-img-anim",
        topic="t",
        status=ProjectStatus.images_ready,
        auto_mode=True,
        general_plan="x" * 200,
        meta=_media_graph_meta(),
    )
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)
    session.add(
        Frame(
            project_id=p.id,
            number=1,
            voiceover_text="hello voiceover for frame one",
            image_prompt="a prompt",
        )
    )
    await session.flush()
    # PNG на диске не обязателен для входа в anim_pr (image_prompt есть).

    await _apply_approve(
        session, p, None, TRANSITIONS[ProjectStatus.images_ready], bot=None
    )
    assert p.status is ProjectStatus.generating_animation_prompts


@pytest.mark.asyncio
async def test_generating_videos_requires_animation_prompts(session, tmp_path) -> None:
    p = Project(
        slug="seq-vid-guard",
        topic="t",
        status=ProjectStatus.images_ready,
        general_plan="x" * 200,
        meta={},
    )
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)
    fr = Frame(
        project_id=p.id,
        number=1,
        voiceover_text="hello voiceover for frame one",
        image_prompt="ready image prompt",
        animation_prompt=None,
    )
    session.add(fr)
    await session.flush()
    img = p.data_dir / "scenes" / "01.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    session.add(
        Artifact(
            project_id=p.id,
            frame_id=fr.id,
            kind=ArtifactKind.scene_image,
            uuid="a" * 24,
            path=str(img),
        )
    )
    await session.flush()

    ok, reason, fix = await can_enter_running(
        session, p, ProjectStatus.generating_videos
    )
    assert ok is False
    assert "anim_pr" in reason or "video prompt" in reason.lower()
    assert fix is ProjectStatus.generating_animation_prompts

    fr.animation_prompt = "camera slowly pushes in, warm light, 5 seconds of motion"
    await session.flush()
    ok2, _, _ = await can_enter_running(session, p, ProjectStatus.generating_videos)
    assert ok2 is True


@pytest.mark.asyncio
async def test_sidecar_marks_anim_pr_noderun_done_when_queue_empty(session) -> None:
    from app.services.anim_pr_sidecar import sync_anim_pr_noderun_done

    wf = Workflow(name="wf-seq", is_default=True, nodes=[], edges=[])
    session.add(wf)
    await session.flush()
    p = Project(
        slug="seq-sidecar",
        topic="t",
        status=ProjectStatus.generating_images,
        meta={},
    )
    session.add(p)
    await session.flush()
    run = WorkflowRun(
        project_id=p.id,
        workflow_id=wf.id,
        status=WorkflowRunStatus.running,
        nodes_snapshot=[{"id": "n_anim", "type": "animation_prompts"}],
        edges_snapshot=[],
    )
    session.add(run)
    await session.flush()
    nr = NodeRun(
        workflow_run_id=run.id,
        node_key="n_anim",
        node_type="animation_prompts",
        status=NodeRunStatus.pending,
    )
    session.add(nr)
    await session.flush()

    with patch(
        "app.services.event_bus.publish_node_event",
        new_callable=AsyncMock,
    ):
        changed = await sync_anim_pr_noderun_done(session, p)
    assert changed is True
    assert nr.status is NodeRunStatus.done
    assert p.status is ProjectStatus.generating_images


@pytest.mark.asyncio
async def test_prepare_exception_does_not_move_project_status(session) -> None:
    """Exception в prepare → Project остаётся на ready."""
    p = Project(
        slug="seq-prep-fail",
        topic="t",
        status=ProjectStatus.images_ready,
        auto_mode=True,
        general_plan="x" * 200,
        meta=_media_graph_meta(),
    )
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)
    session.add(
        Frame(
            project_id=p.id,
            number=1,
            voiceover_text="hello voiceover for frame one",
            image_prompt="a prompt",
        )
    )
    await session.flush()

    with patch(
        "app.services.run_sync.prepare_node_for_step_start",
        new_callable=AsyncMock,
        side_effect=RuntimeError("boom prepare"),
    ):
        await _apply_approve(
            session, p, None, TRANSITIONS[ProjectStatus.images_ready], bot=None
        )
    assert p.status is ProjectStatus.images_ready
