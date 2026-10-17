"""Живой путь границ сцен: run_boundaries → call_opus → chat → _chat_completions_stream.

Исчерпанный бюджет, рубильник и диск останавливают запрос до HTTP.
Повтор call_opus не делается. Проект встаёт на паузу, сцены не меняются.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Base,
    NodeRun,
    NodeRunStatus,
    Project,
    ProjectStatus,
    Scene,
    Workflow,
    WorkflowRun,
    WorkflowRunStatus,
)
from app.services import gpt_api, scene_boundaries as sb, spend_guard
from app.services.step_failure_policy import record_step_failure
from app.settings import settings

_VO = "Альфа бежит по двору. Бета смотрит в окно."


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as opened:
        yield opened
    await engine.dispose()


def _block(monkeypatch: pytest.MonkeyPatch, code: str, message: str) -> dict[str, int]:
    seen = {"n": 0, "http": 0}

    async def limited(**_kwargs: object):
        seen["n"] += 1
        raise spend_guard.PaidCallBlocked(code, message, provider="vibecode", what="chat completions stream")

    def client(*_args: object, **_kwargs: object):
        seen["http"] += 1
        raise AssertionError("HTTP ушёл при закрытом шлюзе")

    from app.services import api_tracker_hook

    monkeypatch.setattr(spend_guard, "paid_gate", limited)
    monkeypatch.setattr(gpt_api, "_async_client", client)
    monkeypatch.setattr(settings, "vibecode_api_key", "vk-test-not-real")
    monkeypatch.setattr(api_tracker_hook, "check_api_allowed", lambda **_kwargs: None)
    return seen


async def _parked_project(session: AsyncSession, tmp_path: Path) -> tuple[Project, NodeRun, Path, list]:
    project = Project(
        slug="sb-live",
        topic="двор",
        status=ProjectStatus.scene_designing,
        auto_mode=False,
        meta={},
    )
    session.add(project)
    await session.flush()
    ready = Scene(
        project_id=project.id,
        sort_key=1,
        title="Двор",
        place="двор",
        meaning="Альфа уже на месте",
    )
    session.add(ready)
    workflow = Workflow(name="sb")
    session.add(workflow)
    await session.flush()
    run = WorkflowRun(
        workflow_id=workflow.id,
        project_id=project.id,
        status=WorkflowRunStatus.running,
    )
    session.add(run)
    await session.flush()
    node = NodeRun(
        workflow_run_id=run.id,
        node_key="boundaries",
        node_type="enrich",
        status=NodeRunStatus.running,
        meta={"kept": True},
    )
    session.add(node)
    await session.flush()
    folder = SimpleNamespace(data_dir=tmp_path / "video")
    saved = sb.save_output(
        folder,
        node_key="boundaries",
        vo=_VO,
        frags=["Альфа бежит по двору.", "Бета смотрит в окно."],
        rep={"check_passed": True},
    )

    async def snapshot() -> list[tuple]:
        rows = (
            await session.execute(
                select(Scene).where(Scene.project_id == project.id).order_by(Scene.sort_key)
            )
        ).scalars().all()
        return [(row.id, row.sort_key, row.title, row.place, row.meaning) for row in rows]

    return project, node, saved, await snapshot()


async def _assert_parked(
    session: AsyncSession,
    project: Project,
    node: NodeRun,
    saved: Path,
    before_rows: list,
    before_file: bytes,
    *,
    reason: str,
) -> None:
    assert project.status is ProjectStatus.paused
    assert project.meta["approval_status"] == "waiting_approval"
    assert project.meta["approval_reason"] == reason
    await session.refresh(node)
    assert node.error is None
    assert node.meta["status"] == "waiting_approval"
    assert node.meta["reason"] == reason
    assert node.meta["kept"] is True
    assert saved.read_bytes() == before_file
    rows = (
        await session.execute(
            select(Scene).where(Scene.project_id == project.id).order_by(Scene.sort_key)
        )
    ).scalars().all()
    assert [(row.id, row.sort_key, row.title, row.place, row.meaning) for row in rows] == before_rows


async def test_live_exhausted_budget_does_not_send_http(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    session: AsyncSession,
) -> None:
    seen = _block(monkeypatch, "spend_cap_run", "потолок за запуск")
    project, node, saved, before_rows = await _parked_project(session, tmp_path)
    before_file = saved.read_bytes()
    with pytest.raises(spend_guard.BudgetExhausted) as raised:
        await sb.run_boundaries(_VO, tmp_path / "trace")
    action = await record_step_failure(session, project, error=raised.value)
    assert action == "waiting_approval"
    assert seen["http"] == 0
    assert seen["n"] == 1
    assert not list((tmp_path / "trace").glob("*.err*.txt"))
    await _assert_parked(
        session, project, node, saved, before_rows, before_file, reason="budget_exhausted",
    )


async def _raise_live(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    session: AsyncSession,
    code: str,
) -> None:
    seen = _block(monkeypatch, code, code)
    project, node, saved, before_rows = await _parked_project(session, tmp_path)
    before_file = saved.read_bytes()
    from app.services.gpt_api import GptApiError

    with pytest.raises(GptApiError) as raised:
        await sb.run_boundaries(_VO, tmp_path / "trace")
    assert raised.value.context["gate_code"] == code
    assert raised.value.context["paid_gate_blocked"] is True
    action = await record_step_failure(session, project, error=raised.value)
    assert action == "waiting_approval"
    assert seen["http"] == 0
    assert seen["n"] == 1
    assert not list((tmp_path / "trace").glob("*.err*.txt"))
    await _assert_parked(
        session, project, node, saved, before_rows, before_file, reason=code,
    )


async def test_live_kill_switch_waits_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    session: AsyncSession,
) -> None:
    await _raise_live(monkeypatch, tmp_path, session, spend_guard.BLOCK_KILL_SWITCH)


async def test_live_disk_block_waits_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    session: AsyncSession,
) -> None:
    await _raise_live(monkeypatch, tmp_path, session, spend_guard.BLOCK_DISK)
