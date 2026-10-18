"""Каркас e2e: ядро и мост зовут адаптер WS2.

Нет ``app.orchestrator.actions`` — модуль пропускается. Адаптер вливается
только во временную ветку и не пушится. Код WS2 здесь не меняется.
"""

from __future__ import annotations

import pytest

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
    """WS2 #385 @ 2c6cf6e0: неизвестное имя — карточка high, не KeyError."""
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
    done = await bridge.execute("montage.delete_frame", params, _grant(ctx, "montage.delete_frame"))
    assert done["status"] == "ok"
    assert done["ok"] is True
    assert done["data"]["marked"] is True
    assert done["data"]["snapshot_id"]
    assert done["change_id"] is None
    assert done["result"]["partial"] is False
    assert done["result"]["skipped"] == []
    assert "montage.delete_frame" in calls


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
