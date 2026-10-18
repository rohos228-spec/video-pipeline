"""problem-статус шага сохраняет change_id конверта."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import PlanStep
from tests.agents.mocks import MockValidators, ScriptedRouter


class _Gate:
    def __init__(self, *, destructive: bool) -> None:
        self.destructive = destructive

    def metadata_of(self, name: str) -> dict[str, object]:
        return {
            "name": name,
            "risk": "low",
            "read_only": False,
            "destructive": self.destructive,
            "paid": False,
        }


_CASES = [
    (
        "unapproved_action",
        {"status": "ok", "result": {"change_id": "chg-keep"}},
        True,
    ),
    (
        "unversioned_edit",
        {
            "status": "ok",
            "result": {
                "change_id": "chg-keep",
                "node_ref": {"node_id": "n_x"},
                "diff": "+x\n",
            },
        },
        False,
    ),
    (
        "soft_delete_unmarked",
        {"status": "ok", "result": {"change_id": "chg-keep", "marked": False}},
        False,
    ),
    (
        "missing_snapshot_ref",
        {"status": "ok", "result": {"change_id": "chg-keep", "marked": True}},
        False,
    ),
    (
        "partial_without_skipped",
        {
            "status": "ok",
            "result": {"change_id": "chg-keep", "partial": True, "skipped": []},
        },
        False,
    ),
    (
        "skipped_without_partial",
        {
            "status": "ok",
            "result": {"change_id": "chg-keep", "partial": False, "skipped": ["n"]},
        },
        False,
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("problem", "result", "destructive"), _CASES)
async def test_problem_status_keeps_change_id(
    token: str,
    problem: str,
    result: dict,
    destructive: bool,
) -> None:
    del token
    runtime = build_runtime(
        actions=_Gate(destructive=destructive),
        validators=MockValidators(),
        router=ScriptedRouter(),
    )
    set_runtime(runtime)
    try:
        task_id = runtime.tasks.insert("problem-id", "шаг", "executing")
        step = PlanStep(action="touch", mutating=True)
        step_id = runtime.tasks.insert_step(task_id, 0, step)
        got, change_id = runtime.supervisor._write_step_done(task_id, step, step_id, result)
        assert got == problem
        assert change_id == "chg-keep"
        row = runtime.tasks.list_steps(task_id)[0]
        assert row["status"] == problem
        assert row["change_id"] == "chg-keep"
    finally:
        set_runtime(None)
