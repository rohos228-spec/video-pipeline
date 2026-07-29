"""После plan/script не гонять встроенный gpt_verdict, если на канвасе
следующая нода — checkMode excel_gpt (иначе 30+ сек «тишины» и порча xlsx)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.models import HITLDecision, HITLKind, HITLRequest, Project, ProjectStatus
from app.orchestrator.auto_advance import maybe_auto_advance
from app.orchestrator.graph.planner import WorkflowGraph
from app.services.auto_review import ReviewResult


def _plan_then_check_meta(*, check_mode: bool = True) -> dict:
    return {
        "canvas_graph": {
            "nodes": [
                {"id": "n_plan", "type": "plan", "position": {"x": 0, "y": 0}, "data": {}},
                {
                    "id": "n_check",
                    "type": "excel_gpt",
                    "position": {"x": 200, "y": 0},
                    "data": {"slotIndex": 1},
                },
                {
                    "id": "n_script",
                    "type": "script",
                    "position": {"x": 400, "y": 0},
                    "data": {},
                },
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "n_plan",
                    "target": "n_check",
                    "sourceHandle": "out",
                    "targetHandle": "in",
                },
                {
                    "id": "e2",
                    "source": "n_check",
                    "target": "n_script",
                    "sourceHandle": "out",
                    "targetHandle": "in",
                },
            ],
        },
        "excel_gpt_nodes": {
            "n_check": {
                "checkMode": check_mode,
                "checkPromptSource": "agent",
                "role": "assist",
            }
        },
    }


def test_graph_next_after_plan_is_check_node() -> None:
    meta = _plan_then_check_meta()
    g = WorkflowGraph(
        meta["canvas_graph"]["nodes"],
        meta["canvas_graph"]["edges"],
    )
    p = Project(slug="t", topic="t", status=ProjectStatus.plan_ready, meta=meta)
    assert g.next_work_node_key_after_ready(p, ProjectStatus.plan_ready) == "n_check"
    assert g.next_running_after_ready(p, ProjectStatus.plan_ready) is ProjectStatus.enriching_1


def test_maybe_auto_advance_skips_builtin_verdict_for_check_node(monkeypatch) -> None:
    from app.orchestrator import auto_advance as aa

    project = Project(
        slug="skip-verdict",
        topic="t",
        status=ProjectStatus.plan_ready,
        auto_mode=True,
        general_plan="x" * 200,
        meta=_plan_then_check_meta(check_mode=True),
    )
    project.id = 27

    hitl = HITLRequest(
        project_id=27,
        kind=HITLKind.approve_plan,
        decision=HITLDecision.pending,
    )
    hitl.payload = {}
    hitl.tg_message_id = None

    class _Sess:
        async def refresh(self, *_a, **_k) -> None:
            return None

        async def flush(self) -> None:
            return None

    verdict_mock = AsyncMock(
        side_effect=AssertionError("builtin verdict must be skipped")
    )
    apply_mock = AsyncMock(return_value=None)

    monkeypatch.setattr(
        "app.services.gen_queue_run.is_user_stopped", lambda _p: False
    )
    monkeypatch.setattr(
        "app.services.project_control.auto_awaits_manual_start", lambda _p: False
    )
    monkeypatch.setattr(
        "app.services.gen_queue.project_gated_by_gen_queue", lambda _id: False
    )
    monkeypatch.setattr(
        "app.services.gen_queue.gen_queue_blocks_project",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.step_cancel.is_generation_active", lambda _id: False
    )
    monkeypatch.setattr(
        aa, "ready_status_confirmed_by_data", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(aa, "clamp_status_to_data", AsyncMock(return_value=None))
    monkeypatch.setattr(aa, "get_latest_hitl", AsyncMock(return_value=hitl))
    monkeypatch.setattr(aa, "_run_verdict_review_for_step", verdict_mock)
    monkeypatch.setattr(aa, "_apply_approve", apply_mock)
    monkeypatch.setattr(
        aa,
        "_next_canvas_node_is_check",
        AsyncMock(return_value="n_check"),
    )

    advanced = asyncio.run(
        maybe_auto_advance(_Sess(), project, bot=None, force=True)
    )

    assert advanced is True
    apply_mock.assert_awaited_once()
    verdict_mock.assert_not_awaited()


def test_maybe_auto_advance_keeps_verdict_when_next_is_script(monkeypatch) -> None:
    from app.orchestrator import auto_advance as aa

    project = Project(
        slug="keep-verdict",
        topic="t",
        status=ProjectStatus.plan_ready,
        auto_mode=True,
        general_plan="x" * 200,
        meta={
            "canvas_graph": {
                "nodes": [
                    {"id": "n_plan", "type": "plan", "data": {}},
                    {"id": "n_script", "type": "script", "data": {}},
                ],
                "edges": [
                    {
                        "id": "e1",
                        "source": "n_plan",
                        "target": "n_script",
                    }
                ],
            }
        },
    )
    project.id = 28

    hitl = HITLRequest(
        project_id=28,
        kind=HITLKind.approve_plan,
        decision=HITLDecision.pending,
    )
    hitl.payload = {}
    hitl.tg_message_id = None

    class _Sess:
        async def refresh(self, *_a, **_k) -> None:
            return None

        async def flush(self) -> None:
            return None

    verdict_mock = AsyncMock(
        return_value=ReviewResult(
            decision=HITLDecision.approved,
            confidence=1.0,
            raw_response="Вердикт: Одобрено",
        )
    )
    apply_review = AsyncMock(return_value=True)

    monkeypatch.setattr(
        "app.services.gen_queue_run.is_user_stopped", lambda _p: False
    )
    monkeypatch.setattr(
        "app.services.project_control.auto_awaits_manual_start", lambda _p: False
    )
    monkeypatch.setattr(
        "app.services.gen_queue.project_gated_by_gen_queue", lambda _id: False
    )
    monkeypatch.setattr(
        "app.services.gen_queue.gen_queue_blocks_project",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.step_cancel.is_generation_active", lambda _id: False
    )
    monkeypatch.setattr(
        aa, "ready_status_confirmed_by_data", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(aa, "clamp_status_to_data", AsyncMock(return_value=None))
    monkeypatch.setattr(aa, "get_latest_hitl", AsyncMock(return_value=hitl))
    monkeypatch.setattr(aa, "_run_verdict_review_for_step", verdict_mock)
    monkeypatch.setattr(aa, "_apply_review_result", apply_review)
    monkeypatch.setattr(
        aa, "_next_canvas_node_is_check", AsyncMock(return_value=None)
    )

    advanced = asyncio.run(
        maybe_auto_advance(_Sess(), project, bot=None, force=True)
    )

    assert advanced is True
    verdict_mock.assert_awaited_once()
    apply_review.assert_awaited_once()
