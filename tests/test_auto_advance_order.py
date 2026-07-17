"""Порядок auto_advance: не прыгать в images / не писать ложный ready-статус."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, ProjectStatus
from app.orchestrator.auto_advance import (
    TRANSITIONS,
    _apply_approve,
    _apply_running_if_data_ok,
)
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


def _plan_to_images_meta() -> dict:
    return {
        "canvas_graph": {
            "nodes": [
                {"id": "n_plan", "type": "plan", "position": {"x": 0, "y": 0}, "data": {}},
                {
                    "id": "n_images",
                    "type": "images",
                    "position": {"x": 300, "y": 0},
                    "data": {},
                },
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "n_plan",
                    "target": "n_images",
                    "sourceHandle": "out",
                    "targetHandle": "in",
                }
            ],
        }
    }


@pytest.mark.asyncio
async def test_apply_running_if_data_ok_does_not_write_false_status(session) -> None:
    """Провал data-guard не должен прыгать plan_ready → frames_ready."""
    p = Project(
        slug="aa-order-1",
        topic="t",
        status=ProjectStatus.plan_ready,
        general_plan="x" * 200,
        meta=_plan_to_images_meta(),
    )
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)
    session.add(
        Frame(
            project_id=p.id,
            number=1,
            voiceover_text="hello voiceover for frame one",
        )
    )
    await session.flush()

    nxt = await _apply_running_if_data_ok(
        session, p, ProjectStatus.generating_images
    )
    assert nxt is None
    assert p.status is ProjectStatus.plan_ready


@pytest.mark.asyncio
async def test_leftover_image_prompt_blocks_early_images(session) -> None:
    """Старый image_prompt не открывает img из plan_ready / script_ready."""
    p = Project(
        slug="aa-order-2",
        topic="t",
        status=ProjectStatus.plan_ready,
        general_plan="x" * 200,
        meta={},
    )
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)
    session.add(
        Frame(
            project_id=p.id,
            number=1,
            voiceover_text="hello voiceover for frame one",
            image_prompt="stale leftover prompt from previous run",
        )
    )
    await session.flush()

    ok, reason, _ = await can_enter_running(
        session, p, ProjectStatus.generating_images
    )
    assert ok is False
    assert "img_pr" in reason


@pytest.mark.asyncio
async def test_images_allowed_after_image_prompts_ready(session) -> None:
    p = Project(
        slug="aa-order-3",
        topic="t",
        status=ProjectStatus.image_prompts_ready,
        general_plan="x" * 200,
        script_text="script",
        meta={},
    )
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)
    session.add(
        Frame(
            project_id=p.id,
            number=1,
            voiceover_text="hello voiceover for frame one",
            image_prompt="ready prompt",
        )
    )
    await session.flush()

    ok, _, _ = await can_enter_running(session, p, ProjectStatus.generating_images)
    assert ok is True


@pytest.mark.asyncio
async def test_approve_plan_default_graph_goes_to_scripting(session) -> None:
    p = Project(
        slug="aa-order-4",
        topic="t",
        status=ProjectStatus.plan_ready,
        general_plan="x" * 200,
        auto_mode=True,
        meta={},
    )
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)

    await _apply_approve(
        session, p, None, TRANSITIONS[ProjectStatus.plan_ready], bot=None
    )
    assert p.status is ProjectStatus.scripting


@pytest.mark.asyncio
async def test_approve_plan_custom_images_edge_stays_without_prompts(session) -> None:
    """plan→images без промптов: не стартуем img и не прыгаем в frames_ready."""
    p = Project(
        slug="aa-order-5",
        topic="t",
        status=ProjectStatus.plan_ready,
        general_plan="x" * 200,
        auto_mode=True,
        meta=_plan_to_images_meta(),
    )
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)
    session.add(
        Frame(
            project_id=p.id,
            number=1,
            voiceover_text="hello voiceover for frame one",
        )
    )
    await session.flush()

    await _apply_approve(
        session, p, None, TRANSITIONS[ProjectStatus.plan_ready], bot=None
    )
    assert p.status is ProjectStatus.plan_ready
