from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Base, HITLDecision, HITLKind, HITLRequest, NodeRun, NodeRunStatus, Project, ProjectStatus, WorkflowRun,
)
from app.orchestrator import auto_advance as aa
from app.services import observation


class _Sess:
    async def refresh(self, *_a, **_k):
        return None

    async def flush(self):
        return None


def _drive(monkeypatch, *, status, kind, meta, verdict_steps=None):
    project = Project(slug="unchecked", topic="t", status=status, auto_mode=True, meta=meta)
    project.id = 41
    hitl = HITLRequest(project_id=41, kind=kind, decision=HITLDecision.pending)
    hitl.payload = {}
    hitl.tg_message_id = None
    events = []
    apply_mock = AsyncMock(return_value=None)

    async def record(session, proj, event_kind, payload):
        events.append((event_kind, payload))
        return True

    monkeypatch.setattr("app.services.gen_queue_run.is_user_stopped", lambda _p: False)
    monkeypatch.setattr("app.services.project_control.auto_awaits_manual_start", lambda _p: False)
    monkeypatch.setattr("app.services.gen_queue.project_gated_by_gen_queue", lambda _id: False)
    monkeypatch.setattr("app.services.gen_queue.gen_queue_blocks_project", AsyncMock(return_value=None))
    monkeypatch.setattr("app.services.step_cancel.is_generation_active", lambda _id: False)
    monkeypatch.setattr(aa, "ready_status_confirmed_by_data", AsyncMock(return_value=True))
    monkeypatch.setattr(aa, "clamp_status_to_data", AsyncMock(return_value=None))
    monkeypatch.setattr(aa, "get_latest_hitl", AsyncMock(return_value=hitl))
    monkeypatch.setattr(aa, "_apply_approve", apply_mock)
    monkeypatch.setattr("app.services.observation.record_project_event", record)
    if verdict_steps is not None:
        monkeypatch.setattr(aa, "READY_VERDICT_STEP", verdict_steps)
    advanced = asyncio.run(aa.maybe_auto_advance(_Sess(), project, bot=None, force=True))
    return advanced, events, apply_mock


@pytest.mark.parametrize("status,kind", [
    (ProjectStatus.hero_ready, HITLKind.approve_hero),
    (ProjectStatus.images_ready, HITLKind.approve_images),
    (ProjectStatus.videos_ready, HITLKind.approve_videos),
])
def test_visual_review_off_is_recorded_as_unchecked(monkeypatch, status, kind):
    advanced, events, apply_mock = _drive(monkeypatch, status=status, kind=kind, meta={"auto_review_kinds": []})
    assert advanced is True
    apply_mock.assert_awaited_once()
    assert len(events) == 1
    event_kind, payload = events[0]
    assert event_kind == "runtime_check"
    assert payload["check"] == "auto_approve_without_review" and payload["performed"] is False
    assert payload["status"] == "unchecked" and payload["reason"] == "visual_review_off"
    assert payload["hitl_kind"] == kind.value and payload["semantic_quality_verified"] is False
    assert payload["summary"].startswith("Автоодобрение без проверки")


def test_review_enabled_but_not_wired_is_also_recorded(monkeypatch):
    advanced, events, apply_mock = _drive(
        monkeypatch, status=ProjectStatus.images_ready, kind=HITLKind.approve_images,
        meta={"auto_review_kinds": ["approve_images"]}, verdict_steps={})
    assert advanced is True
    apply_mock.assert_awaited_once()
    assert [p["reason"] for k, p in events] == ["visual_review_not_implemented"]
    assert "не подключена" in events[0][1]["summary"]


def test_journal_failure_never_blocks_the_approval(monkeypatch):
    project = Project(slug="x", topic="t", status=ProjectStatus.images_ready, meta={})
    project.id = 42
    transition = aa.TRANSITIONS[ProjectStatus.images_ready]

    async def boom(*_a, **_k):
        raise RuntimeError("journal down")

    monkeypatch.setattr("app.services.observation.record_project_event", boom)
    asyncio.run(aa._record_unchecked_auto_approve(_Sess(), project, transition, ProjectStatus.images_ready,
                                                  "visual_review_off"))


def test_event_lands_in_the_real_journal(monkeypatch, tmp_path):
    monkeypatch.setenv("OBSERVER_AUTO_ENABLE", "1")

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'master.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await observation.migrate_observation_schema(conn)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(observation, "_observer",
                            observation.Observer(maker, body_root=tmp_path / "bodies"))
        async with maker() as s:
            project = Project(slug="real-journal", topic="t", status=ProjectStatus.images_ready, meta={})
            s.add(project)
            await s.flush()
            s.add(WorkflowRun(workflow_id=1, project_id=project.id,
                              nodes_snapshot=[{"id": "n1", "type": "images"}], edges_snapshot=[]))
            await s.commit()
            transition = aa.TRANSITIONS[ProjectStatus.images_ready]
            await aa._record_unchecked_auto_approve(s, project, transition, ProjectStatus.images_ready,
                                                    "visual_review_off")
            await s.commit()
            rows = (await s.scalars(select(observation.ObservationOutbox).where(
                observation.ObservationOutbox.kind == "runtime_check"))).all()
        await engine.dispose()
        return rows

    rows = asyncio.run(scenario())
    assert len(rows) == 1 and rows[0].payload["check"] == "auto_approve_without_review"
    assert rows[0].node_key is None and rows[0].payload["performed"] is False
