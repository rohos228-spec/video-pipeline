from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base, NodeRun, NodeRunStatus, WorkflowRun
from app.services import node_status_machine as nsm

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def status_log(tmp_path, monkeypatch):
    path = tmp_path / "logs" / "status.log"
    monkeypatch.setattr(nsm, "STATUS_LOG_PATH", path)
    monkeypatch.delenv("VP_STATUS_LOG_TRANSITIONS", raising=False)
    return path


def _nr(status=NodeRunStatus.pending, key="n_plan", typ="plan"):
    return NodeRun(workflow_run_id=1, node_key=key, node_type=typ, status=status)


def test_format_status_line_is_pure_and_keeps_legacy_format():
    line = nsm.format_status_line(node_key="n1", node_type="plan", old=NodeRunStatus.queued,
                                  new=NodeRunStatus.running, initiator="worker", project_id=7,
                                  ts="2026-10-07T10:00:00")
    assert line == "2026-10-07T10:00:00\tOK\tproject=7\tnode=plan/n1\tqueued → running\tinitiator=worker"
    blocked = nsm.format_status_line(node_key="n1", node_type="plan", old=NodeRunStatus.done,
                                     new=NodeRunStatus.running, initiator="x", project_id=None, blocked=True)
    assert "\tBLOCKED\tproject=?\t" in blocked


def test_every_transition_path_goes_through_single_log_point(status_log):
    nr = _nr()
    assert nsm.queue_node_for_start(nr, project_id=3, initiator="api")
    assert nsm.start_node_running(nr, project_id=3, initiator="api")
    assert nsm.fail_node(nr, "упало", project_id=3)
    assert not nsm.transition_node_status(nr, NodeRunStatus.running, initiator="worker", project_id=3)
    assert nsm.reset_node_to_pending(nr, project_id=3)
    lines = status_log.read_text(encoding="utf-8").splitlines()
    assert [(l.split("\t")[1], l.split("\t")[4]) for l in lines] == [
        ("OK", "pending → queued"), ("OK", "queued → running"), ("OK", "running → failed"),
        ("BLOCKED", "failed → running"), ("OK", "failed → pending"),
    ]


def test_transition_also_stages_observation_record(status_log):
    nr = _nr(NodeRunStatus.queued)
    nsm.start_node_running(nr, project_id=1, initiator="api")
    tr = nr.__dict__["_observation_transitions"]
    assert tr[-1]["from"] == "queued" and tr[-1]["to"] == "running"


def test_transitions_log_can_be_switched_off(status_log, monkeypatch):
    monkeypatch.setenv("VP_STATUS_LOG_TRANSITIONS", "0")
    nr = _nr()
    assert nsm.queue_node_for_start(nr, project_id=1)
    assert not status_log.exists()
    assert nr.status == NodeRunStatus.queued


def test_status_log_is_written_only_by_the_machine():
    offenders = []
    for path in (ROOT / "app").rglob("*.py"):
        if path.name == "node_status_machine.py":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"status\.log", text) and re.search(r"append_rotating|open\(", text):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == [], offenders


def test_derive_status_lines_from_rows():
    a = _nr(NodeRunStatus.failed, "n2", "script")
    a.error = "плохо\nочень"
    a.attempts = 2
    a.finished_at = datetime(2026, 10, 7, 12, 0, 0)
    b = _nr(NodeRunStatus.done, "n1", "plan")
    b.started_at = datetime(2026, 10, 7, 11, 0, 0)
    lines = nsm.derive_status_lines([(a, 5), (b, None)])
    assert lines == [
        "2026-10-07T11:00:00\tSNAPSHOT\tproject=?\tnode=plan/n1\tdone\tattempts=0",
        "2026-10-07T12:00:00\tSNAPSHOT\tproject=5\tnode=script/n2\tfailed\tattempts=2\terror=плохо очень",
    ]


@pytest.fixture
async def sessions(tmp_path):
    from app.services import actions_v1
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_snapshot_is_rebuilt_from_node_runs_table(sessions, tmp_path):
    async with sessions() as s:
        s.add_all([WorkflowRun(id=1, workflow_id=1, project_id=10), WorkflowRun(id=2, workflow_id=1, project_id=20)])
        await s.flush()
        s.add_all([
            NodeRun(workflow_run_id=1, node_key="a", node_type="plan", status=NodeRunStatus.done),
            NodeRun(workflow_run_id=1, node_key="b", node_type="script", status=NodeRunStatus.failed, error="e"),
            NodeRun(workflow_run_id=2, node_key="a", node_type="plan", status=NodeRunStatus.running),
        ])
        await s.commit()
    target = tmp_path / "logs" / "snap.log"
    async with sessions() as s:
        assert await nsm.rebuild_status_snapshot(s, path=target) == 3
        assert await nsm.rebuild_status_snapshot(s, project_id=10, path=target) == 2
    rows = [l.split("\t") for l in target.read_text(encoding="utf-8").splitlines()]
    assert {(r[2], r[3], r[4]) for r in rows} == {("project=10", "node=plan/a", "done"),
                                                  ("project=10", "node=script/b", "failed")}
    assert not target.with_name(target.name + ".tmp").exists()
