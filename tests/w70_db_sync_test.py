"""W70: синхронизация двух БД и потолок цепочки (аудит W56 K1, K5, K6, K7)."""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import project_db as pdb
from app import settings as app_settings
from app.models import Base, Frame, FrameStatus, HITLDecision, HITLKind, HITLRequest, NodeRunStatus, Project, ProjectStatus
from app.services import hitl_apply


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


@pytest.fixture(autouse=True)
def _no_fact():
    with patch("app.services.observation.record_fact", new=AsyncMock()):
        yield


def _node(status, start, finish):
    return SimpleNamespace(status=status, started_at=start, finished_at=finish)


# ---- K5: защита от устаревшего master не зависит от числа узлов
def test_pick_completed_node_several_nodes_takes_latest_done():
    t = datetime(2026, 1, 1)
    old = _node(NodeRunStatus.done, t, t + timedelta(minutes=1))
    new = _node(NodeRunStatus.done, t + timedelta(hours=1), t + timedelta(hours=1, minutes=2))
    assert pdb._pick_completed_node([old, new]) is new
    assert pdb._pick_completed_node([new, old]) is new


def test_pick_completed_node_running_or_empty_means_no_guard():
    t = datetime(2026, 1, 1)
    done = _node(NodeRunStatus.done, t, t)
    running = _node(NodeRunStatus.running, t, None)
    assert pdb._pick_completed_node([done, running]) is None
    assert pdb._pick_completed_node([]) is None
    assert pdb._pick_completed_node([_node(NodeRunStatus.done, None, None)]) is None


# ---- K1: счётчики отражаются project.db -> master, не наоборот
def test_counters_mirrored_to_master_only_when_requested():
    src = Project(slug="a", topic="t", status=ProjectStatus.paused, meta={"restart_chain": {"images": {"count": 3}}})
    dest = Project(slug="b", topic="t", status=ProjectStatus.new, meta={"regen_budget": {"x": 1}, "title_note": 1})
    pdb._apply_runtime_fields(src, dest)
    assert "restart_chain" not in dest.meta and dest.meta["regen_budget"] == {"x": 1}
    pdb._apply_runtime_fields(src, dest, mirror_counters=True)
    assert dest.meta["restart_chain"]["images"]["count"] == 3
    assert "regen_budget" not in dest.meta and dest.meta["title_note"] == 1


# ---- K5: сбой push записывается и снимается при успехе
async def test_push_failure_is_recorded_then_cleared(session):
    p = Project(slug="pf", topic="t", status=ProjectStatus.paused, meta={})
    session.add(p)
    await session.flush()
    with patch.object(pdb, "push_runtime_to_master", new=AsyncMock(return_value=False)):
        assert await pdb.push_runtime_to_master_recorded(session, p) is False
        assert await pdb.push_runtime_to_master_recorded(session, p) is False
    assert p.meta[pdb.SYNC_PENDING_KEY]["failures"] == 2
    with patch.object(pdb, "push_runtime_to_master", new=AsyncMock(return_value=True)):
        assert await pdb.push_runtime_to_master_recorded(session, p) is True
    assert pdb.SYNC_PENDING_KEY not in p.meta


async def test_push_ok_leaves_meta_untouched(session):
    p = Project(slug="po", topic="t", status=ProjectStatus.paused, meta={"k": 1})
    session.add(p)
    await session.flush()
    with patch.object(pdb, "push_runtime_to_master", new=AsyncMock(return_value=True)):
        assert await pdb.push_runtime_to_master_recorded(session, p) is True
    assert p.meta == {"k": 1}


async def test_push_runtime_to_master_returns_true_on_non_project_db(session):
    p = Project(slug="pm", topic="t", status=ProjectStatus.paused)
    assert await pdb.push_runtime_to_master(session, p) is True  # не project.db: нечего отправлять


# ---- K6: порядок в advance_runner - commit project.db раньше push в master
def test_advance_runner_commits_before_push():
    import inspect
    from app.services import advance_runner
    src = inspect.getsource(advance_runner.advance_project_job)
    assert src.index("commit_with_retry(session)") < src.index("push_runtime_to_master_recorded(session, project)")


# ---- K7: решение «перегенерировать» не теряется на потолке цепочки
async def test_manual_regen_at_ceiling_returns_request_to_pending(session, monkeypatch):
    monkeypatch.setattr(app_settings.settings, "restart_chain_max_per_node", 1)
    p = Project(slug="k7", topic="t", status=ProjectStatus.images_ready, hero_mode="no_hero", meta={
        "restart_chain": {"generating_images": {"count": 1, "by": {"manual": 1}}}})
    session.add(p)
    await session.flush()
    f = Frame(project_id=p.id, number=1, voiceover_text="v", status=FrameStatus.image_generated)
    session.add(f)
    await session.flush()
    req = HITLRequest(project_id=p.id, frame_id=f.id, kind=HITLKind.approve_images, payload={})
    session.add(req)
    await session.flush()
    assert await hitl_apply.claim_hitl_decision(session, req, HITLDecision.regenerate, actor="u")
    assert req.decision is HITLDecision.regenerate
    await hitl_apply.apply_hitl_side_effects(session, req, HITLDecision.regenerate)
    assert p.status == ProjectStatus.paused
    assert req.decision is HITLDecision.pending and req.decided_at is None
    assert req.payload["blocked_decision"] == "regenerate"
    assert f.status == FrameStatus.image_generated  # кадр не тронут


async def test_manual_regen_below_ceiling_keeps_decision(session, monkeypatch):
    monkeypatch.setattr(app_settings.settings, "restart_chain_max_per_node", 5)
    p = Project(slug="k7b", topic="t", status=ProjectStatus.images_ready, hero_mode="no_hero", meta={})
    session.add(p)
    await session.flush()
    f = Frame(project_id=p.id, number=1, voiceover_text="v", status=FrameStatus.image_generated)
    session.add(f)
    await session.flush()
    req = HITLRequest(project_id=p.id, frame_id=f.id, kind=HITLKind.approve_images, payload={})
    session.add(req)
    await session.flush()
    assert await hitl_apply.claim_hitl_decision(session, req, HITLDecision.regenerate, actor="u")
    await hitl_apply.apply_hitl_side_effects(session, req, HITLDecision.regenerate)
    assert req.decision is HITLDecision.regenerate
    assert f.status == FrameStatus.image_prompt_ready
