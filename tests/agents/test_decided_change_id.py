"""Ядровой change_id привязан к шагу и только к approve/retry_step."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.supervisor import ApprovalDecision
from app.orchestrator.contracts import PlanStep
from tests.agents.mocks import MockValidators, ScriptedRouter


class _PaidRegistry:
    def metadata_of(self, name: str) -> dict[str, object]:
        return {"name": name, "paid": True, "destructive": False, "read_only": False, "risk": "low"}


def _ok(action: str) -> dict:
    return {"status": "ok", "cost": 0, "result": {"action": action}}


@pytest.mark.asyncio
async def test_rejected_or_foreign_card_does_not_approve_the_step(token: str) -> None:
    del token
    runtime = build_runtime(actions=_PaidRegistry(), validators=MockValidators(), router=ScriptedRouter())
    set_runtime(runtime)
    session = "decided-step"
    try:
        task_id = runtime.tasks.insert(session, "карта", "waiting_approval")
        runtime.supervisor._session_task[session] = task_id
        step = PlanStep(action="charge", paid=True)
        step_id = runtime.tasks.insert_step(task_id, 0, step)
        sup = runtime.supervisor
        sup._appr_loaded.add(task_id)

        sup._appr_open[task_id] = f"orch-appr-{task_id}-1"
        sup._card_action[sup._appr_open[task_id]] = "charge"
        await sup._emit_decision(task_id, session, ApprovalDecision.deny)
        problem, _change = sup._write_step_done(task_id, step, step_id, _ok("charge"))
        assert problem == "unapproved_action"
        assert (task_id, "charge") not in sup._decided_change_id

        sup._appr_open[task_id] = f"orch-appr-{task_id}-2"
        sup._card_action[sup._appr_open[task_id]] = "look"
        await sup._emit_decision(task_id, session, None)
        problem, _change = sup._write_step_done(task_id, step, step_id, _ok("charge"))
        assert problem == "unapproved_action"

        sup._appr_open[task_id] = f"orch-appr-{task_id}-3"
        sup._card_action[sup._appr_open[task_id]] = "charge"
        await sup._emit_decision(task_id, session, ApprovalDecision.approve)
        problem, change_id = sup._write_step_done(task_id, step, step_id, _ok("charge"))
        assert problem is None
        assert change_id == f"orch-appr-{task_id}-3"
        assert (task_id, "charge") not in sup._decided_change_id
        again, _change = sup._write_step_done(task_id, step, step_id, _ok("charge"))
        assert again == "unapproved_action"
    finally:
        set_runtime(None)
