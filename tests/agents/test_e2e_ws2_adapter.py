"""Каркас e2e: ядро и мост зовут адаптер WS2.

Нет ``app.orchestrator.actions`` — модуль пропускается. Адаптер вливается
только во временную ветку и не пушится. Код WS2 здесь не меняется.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import func, select

pytest.importorskip("app.orchestrator.actions")

from tests.action_layer_support import env, make_spec  # noqa: E402

from app.orchestrator.agents.runtime import build_runtime, set_runtime  # noqa: E402
from app.orchestrator.agents.wiring import ActionsBridge, ModuleActions  # noqa: E402
from app.orchestrator.contracts import PlanStep, TaskStatus  # noqa: E402
from tests.agents.mocks import FixedPlanner, MockValidators, ScriptedRouter  # noqa: E402

__all__ = ["env"]


def _bridge(monkeypatch):
    import app.orchestrator.actions as actions

    calls: list[str] = []
    contexts: list[dict] = []
    real = actions.execute

    async def spy(name, params, ctx):
        calls.append(name)
        contexts.append(dict(ctx))
        return await real(name, params, ctx)

    monkeypatch.setattr(actions, "execute", spy)
    return ActionsBridge(ModuleActions(actions)), calls, contexts


def _grant(
    ctx: dict,
    action: str,
    change_id: str = "orch-appr-7-1",
    *,
    cc_change_id: str | None = None,
) -> dict:
    return {
        **ctx,
        "approval": {
            "change_id": change_id,
            "action": action,
            "risk": "high",
            "destructive": True,
            "granted": True,
            "cc_change_id": cc_change_id,
        },
    }


def test_e2e_unknown_metadata_is_high_card_not_keyerror() -> None:
    """WS2 #385 @ e2af5b4c: неизвестное имя — карточка high, не KeyError."""
    from app.orchestrator.actions import metadata_of, whitelist

    card = metadata_of("frobnicate")
    assert "frobnicate" not in whitelist()
    assert card.risk == "high"
    assert card.destructive is True
    assert card.paid is True


@pytest.mark.asyncio
async def test_e2e_read_through_core(env, monkeypatch, token: str) -> None:
    del token
    bridge, calls, _contexts = _bridge(monkeypatch)
    runtime = build_runtime(
        actions=bridge,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [PlanStep(action="history.list_snapshots", params={"limit": 5}, risk="none")]
        ),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("e2e-read", "снимки")
        await runtime.supervisor.wait(task_id, timeout=5)
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.done.value
        assert calls == ["history.list_snapshots"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_e2e_mutation_runs_only_with_grant(env, monkeypatch) -> None:
    from app.services.action_layer.spec import registry

    ran: list[str] = []

    async def _run(_ctx, _params):
        ran.append("run")
        return {"ok": True, "marker": "ran"}

    spec = make_spec("adapter.e2e_mutate", _run, risk="local_safe")
    registry.register(spec)
    ctx = {"app": env.app, "operator": "e2e"}
    try:
        bridge, calls, contexts = _bridge(monkeypatch)
        held = await bridge.execute("adapter.e2e_mutate", {}, ctx)
        assert held["status"] == "approval_required"
        assert held["ok"] is False
        assert held["error"] is None
        assert held["data"]["metadata"]["risk"] == "high"
        assert held["data"]["metadata"]["destructive"] is True
        assert ran == []
        done = await bridge.execute(
            "adapter.e2e_mutate",
            {},
            _grant(ctx, "adapter.e2e_mutate", cc_change_id="cc-e2e-1"),
        )
        assert done["ok"] is True
        assert done["status"] == "ok"
        assert done["data"]["marker"] == "ran"
        assert done["change_id"] is None
        assert done["cc_change_id"] == "cc-e2e-1"
        approval = contexts[-1]["approval"]
        assert approval["change_id"] == "orch-appr-7-1"
        assert approval["cc_change_id"] == "cc-e2e-1"
        assert ran == ["run"]
        assert calls == ["adapter.e2e_mutate", "adapter.e2e_mutate"]
    finally:
        registry.unregister("adapter.e2e_mutate")


@pytest.mark.asyncio
async def test_e2e_soft_delete_needs_grant_and_core_change_id(env, monkeypatch) -> None:
    from app.models import Frame

    bridge, calls, _contexts = _bridge(monkeypatch)
    pid = await env.add_project(frames=1)
    ids = await env.frame_ids(pid)
    async with env.factory() as session:
        frame = await session.get(Frame, ids[1])
        frame.attrs = {"кадры": [{"id": "s1", "номер": 1, "активен": True, "текст": "первый"}]}
        await session.commit()
    ctx = {"app": env.app, "operator": "e2e"}
    params = {"project_id": pid, "frame_id": ids[1]}
    held = await bridge.execute("montage.delete_frame", params, ctx)
    assert held["status"] == "approval_required"
    assert held["error"] is None
    async with env.factory() as session:
        frame = await session.get(Frame, ids[1])
        assert frame.attrs["кадры"][0]["активен"] is True
    bad = await bridge.execute(
        "montage.delete_frame",
        params,
        _grant(ctx, "montage.delete_frame", change_id="orch-appr-task-1"),
    )
    assert bad["status"] == "failed"
    assert bad["ok"] is False
    done = await bridge.execute(
        "montage.delete_frame",
        params,
        _grant(ctx, "montage.delete_frame", cc_change_id="cc-frame-1"),
    )
    assert done["status"] == "ok"
    assert done["ok"] is True
    assert done["data"]["marked"] is True
    assert done["data"]["restore_action"] == "frame.restore"
    assert done["data"]["snapshot_id"]
    assert done["change_id"] is None
    assert done["cc_change_id"] == "cc-frame-1"
    assert done["result"]["partial"] is False
    assert done["result"]["skipped"] == []
    async with env.factory() as session:
        frame = await session.get(Frame, ids[1])
        assert frame is not None
        assert frame.attrs["кадры"][0]["активен"] is False
        total = await session.scalar(select(func.count()).select_from(Frame).where(Frame.project_id == pid))
        assert total == 1
    assert "montage.delete_frame" in calls


_CHANGE_ID = re.compile(r"^orch-appr-[0-9]+-[0-9]+$")


@pytest.mark.asyncio
async def test_e2e_delete_frame_through_core_keeps_the_row(env, monkeypatch) -> None:
    """Ядро выдаёт change_id, адаптер только помечает кадр."""
    from app.orchestrator.actions import metadata_of

    from app.models import Frame

    for name in ("montage.delete_frame", "montage.delete_child_frame"):
        card = metadata_of(name)
        assert card.soft_delete is True
        assert card.restore_action == "frame.restore"
    bridge, calls, contexts = _bridge(monkeypatch)
    pid = await env.add_project(frames=1)
    ids = await env.frame_ids(pid)
    async with env.factory() as session:
        frame = await session.get(Frame, ids[1])
        frame.attrs = {"кадры": [{"id": "s1", "номер": 1, "активен": True, "текст": "первый"}]}
        await session.commit()
    runtime = build_runtime(
        actions=bridge,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner(
            [
                PlanStep(
                    action="montage.delete_frame",
                    params={"project_id": pid, "frame_id": ids[1]},
                    mutating=True,
                    risk="high",
                )
            ]
        ),
    )
    set_runtime(runtime)
    session_id = "e2e-delete"
    try:
        task_id = await runtime.supervisor.start_task(session_id, "пометь кадр", project_id=pid)
        await runtime.supervisor.wait(task_id, timeout=5)
        assert calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.waiting_approval.value
        opened = [event for event in runtime.bus.replay(session_id, 0) if event.type == "approval_required"]
        assert opened
        change_id = opened[-1].payload["change_id"]
        assert _CHANGE_ID.fullmatch(change_id)
        await runtime.supervisor.grant_approval(session_id)
        await runtime.supervisor.wait(task_id, timeout=5)
        row = runtime.tasks.get(task_id)
        assert row["status"] == TaskStatus.done.value, row
        assert calls == ["montage.delete_frame"]
        assert contexts[-1]["approval"]["change_id"] == change_id
        assert _CHANGE_ID.fullmatch(contexts[-1]["approval"]["change_id"])
        async with env.factory() as session:
            frame = await session.get(Frame, ids[1])
            assert frame is not None
            assert frame.attrs["кадры"][0]["активен"] is False
            total = await session.scalar(
                select(func.count()).select_from(Frame).where(Frame.project_id == pid)
            )
            assert total == 1
    finally:
        set_runtime(None)


def _raw_bridge(monkeypatch):
    import app.orchestrator.actions as actions

    calls: list[str] = []
    contexts: list[dict] = []
    raw: list[object] = []
    real = actions.execute

    async def spy(name, params, ctx):
        calls.append(name)
        contexts.append(dict(ctx) if isinstance(ctx, dict) else {})
        out = await real(name, params, ctx)
        raw.append(out)
        return out

    monkeypatch.setattr(actions, "execute", spy)
    return ActionsBridge(ModuleActions(actions)), calls, contexts, raw


async def _run_granted(
    bridge,
    session_id: str,
    goal: str,
    step: PlanStep,
    *,
    project_id: int,
    cc_change_id: str | None = None,
):
    runtime = build_runtime(
        actions=bridge,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([step]),
    )
    set_runtime(runtime)
    task_id = await runtime.supervisor.start_task(session_id, goal, project_id=project_id)
    await runtime.supervisor.wait(task_id, timeout=5)
    held = runtime.tasks.get(task_id)
    assert held["status"] == TaskStatus.waiting_approval.value, (held.get("status"), held.get("error"))
    opened = [event for event in runtime.bus.replay(session_id, 0) if event.type == "approval_required"]
    assert opened
    change_id = opened[-1].payload["change_id"]
    assert _CHANGE_ID.fullmatch(change_id)
    if cc_change_id is not None:
        runtime.supervisor._cc_change_id[(task_id, step.action)] = cc_change_id
    await runtime.supervisor.grant_approval(session_id)
    await runtime.supervisor.wait(task_id, timeout=5)
    return runtime, task_id, change_id


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "lookup"),
    [("montage.delete_frame", "snapshot_id"), ("montage.delete_child_frame", "change_id")],
)
async def test_e2e_frame_restore_roundtrip(env, monkeypatch, action: str, lookup: str) -> None:
    """delete → грант ядра → frame.restore → повторный restore не успех."""
    from app.models import Frame

    original = [{"id": "s1", "номер": 1, "активен": True, "текст": "первый"}]
    bridge, calls, contexts, raw = _raw_bridge(monkeypatch)
    mapped: list[dict] = []
    real_bridge = bridge.execute

    async def _track(name, params, ctx):
        out = await real_bridge(name, params, ctx)
        mapped.append(out)
        return out

    bridge.execute = _track
    pid = await env.add_project(frames=1)
    ids = await env.frame_ids(pid)
    async with env.factory() as session:
        frame = await session.get(Frame, ids[1])
        frame.attrs = {"кадры": [dict(shot) for shot in original]}
        await session.commit()
    delete_params = {"project_id": pid}
    if action == "montage.delete_frame":
        delete_params["frame_id"] = ids[1]
    else:
        delete_params["frame_number"] = 1
    try:
        _runtime, task_id, _card = await _run_granted(
            bridge,
            f"e2e-del-{lookup}",
            "пометь",
            PlanStep(action=action, params=delete_params, mutating=True, risk="high"),
            project_id=pid,
        )
        assert runtime_status(task_id, _runtime) == TaskStatus.done.value
        assert calls == [action]
        payload = raw[-1]
        assert isinstance(payload, dict)
        body = payload["result"]
        assert body["marked"] is True
        assert body["restore_action"] == "frame.restore"
        async with env.factory() as session:
            frame = await session.get(Frame, ids[1])
            assert frame is not None
            shots = [dict(shot) for shot in frame.attrs["кадры"]]
            assert shots[0]["активен"] is False
            shots[0]["текст"] = "после удаления"
            frame.attrs = {"кадры": shots}
            await session.commit()
        edited = [{"id": "s1", "номер": 1, "активен": True, "текст": "после удаления"}]
        restore_params = {
            "project_id": pid,
            lookup: body["snapshot_id"] if lookup == "snapshot_id" else body["change_id"],
        }
        calls.clear()
        _runtime, task_id, _card = await _run_granted(
            bridge,
            f"e2e-restore-{lookup}",
            "верни",
            PlanStep(action="frame.restore", params=restore_params, mutating=True, risk="high"),
            project_id=pid,
            cc_change_id="cc-keep",
        )
        assert runtime_status(task_id, _runtime) == TaskStatus.done.value
        assert calls == ["frame.restore"]
        assert contexts[-1]["approval"]["action"] == "frame.restore"
        assert contexts[-1]["approval"]["cc_change_id"] == "cc-keep"
        assert _CHANGE_ID.fullmatch(contexts[-1]["approval"]["change_id"])
        restored = raw[-1]
        assert isinstance(restored, dict)
        assert restored["cc_change_id"] == "cc-keep"
        assert mapped[-1]["cc_change_id"] == "cc-keep"
        async with env.factory() as session:
            frame = await session.get(Frame, ids[1])
            assert frame is not None
            assert frame.attrs["кадры"] == edited
            total = await session.scalar(select(func.count()).select_from(Frame).where(Frame.project_id == pid))
            assert total == 1
        calls.clear()
        _runtime, task_id, _card = await _run_granted(
            bridge,
            f"e2e-again-{lookup}",
            "ещё раз",
            PlanStep(action="frame.restore", params=dict(restore_params), mutating=True, risk="high"),
            project_id=pid,
        )
        row = _runtime.tasks.get(task_id)
        assert row["status"] == TaskStatus.failed.value
        assert row["status"] != TaskStatus.done.value
        steps = _runtime.tasks.list_steps(task_id)
        assert steps
        assert all(item["status"] != "done" for item in steps)
        async with env.factory() as session:
            frame = await session.get(Frame, ids[1])
            assert frame.attrs["кадры"] == edited
    finally:
        set_runtime(None)


def runtime_status(task_id: int, runtime) -> str:
    return runtime.tasks.get(task_id)["status"]


@pytest.mark.asyncio
async def test_e2e_partial_skipped_are_read_from_result(env, monkeypatch) -> None:
    """partial и skipped читаются из result. Соседнее data не подменяет их."""
    del env
    import app.orchestrator.actions as actions
    from app.orchestrator.action_contracts.contracts import ActionResult as Envelope
    from app.orchestrator.action_contracts.contracts import SoftDeleteResult

    body = SoftDeleteResult(
        change_id="orch-appr-7-1",
        action="montage.delete_frame",
        marked=True,
        restore_action="frame.restore",
        snapshot_id="snap-e2e",
        partial=True,
        skipped=["shot-b"],
    )
    envelope = Envelope(status="ok", result=body, error=None, warnings=[])
    object.__setattr__(envelope, "data", {"partial": False, "skipped": ["decoy"]})

    async def _return(_name, _params, _ctx):
        return envelope

    monkeypatch.setattr(actions, "execute", _return)
    out = await ActionsBridge(ModuleActions(actions)).execute(
        "montage.delete_frame",
        {"project_id": 1, "frame_id": 1},
        {"operator": "e2e"},
    )
    assert out["status"] == "partial"
    assert out["ok"] is False
    assert out["result"]["partial"] is True
    assert out["result"]["skipped"] == ["shot-b"]
    assert out["data"]["skipped"] == ["shot-b"]
    assert "decoy" not in out["data"]["skipped"]


@pytest.mark.asyncio
async def test_e2e_unknown_name_is_refused(env, monkeypatch) -> None:
    """Канон — whitelist(). Карточка metadata_of вызов не открывает."""
    del env
    from app.orchestrator.actions import metadata_of

    metadata_of("frobnicate")
    bridge, calls, _contexts = _bridge(monkeypatch)
    out = await bridge.execute("frobnicate", {}, {"operator": "e2e"})
    assert out.ok is False
    assert out.data["error"] == "not_in_whitelist"
    assert calls == []


@pytest.mark.asyncio
async def test_e2e_restore_follows_whitelist_and_unlisted_is_refused(monkeypatch) -> None:
    """WS2 #385 @ e2af5b4c: frame.restore только из whitelist(), чужое имя — отказ.

    group.delete и prompt.delete_master до R2 отвечают failed, не успех.
    """
    import app.orchestrator.actions as actions
    import app.orchestrator.contracts as contracts
    from app.orchestrator.actions import execute, whitelist
    from app.orchestrator.contracts import PlanStep, not_in_whitelist, step_is_deletion

    assert not hasattr(contracts, "frame_restore_grant")
    listed = whitelist()
    assert "frame.restore" in listed
    assert "group.delete" not in listed
    assert "prompt.delete_master" not in listed
    bridge, calls, _contexts = _bridge(monkeypatch)
    assert not_in_whitelist("frame.restore", bridge) is False
    assert step_is_deletion(PlanStep(action="frame.restore", mutating=True, risk="high"), bridge) is False
    held = await bridge.execute("frame.restore", {"project_id": 1}, {"operator": "e2e"})
    assert held["status"] == "approval_required"
    assert held["ok"] is False
    assert calls == ["frame.restore"]
    refused = await bridge.execute("not.a.real.action", {}, {"operator": "e2e"})
    assert refused.ok is False
    assert refused.data["error"] == "not_in_whitelist"
    assert calls == ["frame.restore"]
    for name in ("group.delete", "prompt.delete_master"):
        denied = await execute(name, {}, {"operator": "e2e"})
        assert isinstance(denied, dict)
        assert denied["status"] == "failed"
        assert denied["error"] == "до R2 недоступно"
