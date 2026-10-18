"""Мост r2.3: ok/data/cost, код ошибки, partial."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.agents.wiring import ActionsBridge
from app.orchestrator.contracts import ActionResult, PlanStep, TaskStatus, envelope_warnings
from tests.agents.mocks import FixedPlanner, MockValidators, ScriptedRouter


class _Inner:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def metadata_of(self, name: str) -> dict[str, object]:
        return {"name": name, "risk": "none", "read_only": True, "destructive": False, "paid": False}

    async def execute(self, name: str, params: dict, task_ctx: dict) -> dict:
        del name, params, task_ctx
        return self.payload


@pytest.mark.asyncio
async def test_r23_ok_maps_to_ok_data_cost_and_warnings() -> None:
    out = await ActionsBridge(
        _Inner(
            {
                "status": "ok",
                "result": {"seen": 1},
                "cost": 0.25,
                "warnings": ["keep"],
                "error": None,
            }
        )
    ).execute("look", {}, {})
    assert out["ok"] is True
    assert out["data"] == {"seen": 1}
    assert out["cost"] == 0.25
    assert out["warnings"] == ["keep"]
    assert envelope_warnings(out) == ["keep"]


@pytest.mark.asyncio
async def test_r23_error_becomes_failed_with_code() -> None:
    out = await ActionsBridge(
        _Inner({"status": "ok", "result": {}, "error": {"code": "disk_full"}, "warnings": []})
    ).execute("look", {}, {})
    assert out["ok"] is False
    assert out["status"] == "failed"
    assert out["error"] == "disk_full"


@pytest.mark.asyncio
async def test_r23_change_control_id_is_cc_change_id() -> None:
    out = await ActionsBridge(
        _Inner(
            {
                "status": "ok",
                "result": {"change_id": "cc-42", "snapshot_id": "snap"},
                "cost": 0,
                "warnings": [],
                "error": None,
            }
        )
    ).execute("look", {}, {})
    assert out["change_id"] is None
    assert out["cc_change_id"] == "cc-42"
    assert "change_id" not in out["data"]
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_action_result_change_id_moves_to_cc_change_id() -> None:
    class _Result:
        def metadata_of(self, name: str) -> dict[str, object]:
            return {"name": name, "risk": "none", "read_only": True, "destructive": False, "paid": False}

        async def execute(self, name: str, params: dict, task_ctx: dict) -> ActionResult:
            del name, params, task_ctx
            return ActionResult(ok=True, data={"n": 1}, change_id="cc-9", cost=1.0)

    out = await ActionsBridge(_Result()).execute("look", {}, {})
    assert out["change_id"] is None
    assert out["cc_change_id"] == "cc-9"
    assert out["ok"] is True
    assert out["cost"] == 1.0


@pytest.mark.asyncio
async def test_r23_partial_true_is_status_partial() -> None:
    out = await ActionsBridge(
        _Inner(
            {
                "status": "ok",
                "result": {"partial": True, "skipped": ["take-2"]},
                "cost": 0,
                "warnings": [],
                "error": None,
            }
        )
    ).execute("look", {}, {})
    assert out["status"] == "partial"
    assert out["ok"] is False
    assert out["data"]["skipped"] == ["take-2"]
    assert out["cost"] == 0.0


class _Body:
    """Тело конверта WS2: объект с model_dump, не dict ядра."""

    def __init__(self, **fields: object) -> None:
        self._fields = fields

    def model_dump(self) -> dict[str, object]:
        return dict(self._fields)


class _Envelope:
    """ActionResult ws2-r2.3: status/result/error/warnings, без ok/data/cost."""

    def __init__(
        self,
        status: str,
        result: object,
        *,
        error: object = None,
        warnings: list[str] | None = None,
        cost: float = 0.0,
        data: dict[str, object] | None = None,
    ) -> None:
        self.status = status
        self.result = result
        self.error = error
        self.warnings = [] if warnings is None else warnings
        self.cost = cost
        self.data = data


class _ObjectAdapter:
    def __init__(self, payload: _Envelope) -> None:
        self.payload = payload
        self.calls: list[str] = []

    def metadata_of(self, name: str) -> dict[str, object]:
        return {
            "name": name,
            "risk": "none",
            "read_only": True,
            "destructive": False,
            "paid": False,
        }

    async def execute(self, name: str, params: dict, task_ctx: dict) -> _Envelope:
        del params, task_ctx
        self.calls.append(name)
        return self.payload


@pytest.mark.asyncio
async def test_object_envelope_maps_status_to_ok_data_cost() -> None:
    ok = await ActionsBridge(
        _ObjectAdapter(_Envelope("ok", _Body(seen=1), cost=1.5, warnings=["keep"]))
    ).execute("look", {}, {})
    assert ok["ok"] is True
    assert ok["status"] == "ok"
    assert ok["data"] == {"seen": 1}
    assert ok["cost"] == 1.5
    assert ok["warnings"] == ["keep"]

    failed = await ActionsBridge(
        _ObjectAdapter(_Envelope("failed", None, error="нет"))
    ).execute("look", {}, {})
    assert failed["ok"] is False
    assert failed["status"] == "failed"
    assert failed["error"] == "нет"
    assert failed["cost"] == 0.0


@pytest.mark.asyncio
async def test_object_approval_required_is_escalation_not_error(token: str) -> None:
    del token
    actions = ActionsBridge(
        _ObjectAdapter(_Envelope("approval_required", _Body(reason="нужно подтверждение"), error=None))
    )
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="look", risk="none")]),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("r23-hold", "смотри")
        await runtime.supervisor.wait(task_id, timeout=5)
        row = runtime.tasks.get(task_id)
        assert row["status"] == TaskStatus.waiting_approval.value
        assert row["error"] != "step_failed"
        opened = [
            event
            for event in runtime.bus.replay("r23-hold", 0)
            if event.type == "approval_required" and event.payload.get("reason") == "approval_required"
        ]
        assert opened
        assert actions.inner.calls == ["look"]
    finally:
        set_runtime(None)


@pytest.mark.asyncio
async def test_envelope_cc_change_id_is_copied_as_its_own_key() -> None:
    """cc_change_id — отдельный ключ конверта, не change_id ядра."""
    envelope = _Envelope("ok", _Body(seen=1, change_id="orch-appr-7-1"), cost=0.0)
    envelope.cc_change_id = "cc-e2e-1"
    out = await ActionsBridge(_ObjectAdapter(envelope)).execute("look", {}, {})
    assert out["ok"] is True
    assert out["change_id"] is None
    assert out["cc_change_id"] == "cc-e2e-1"
    assert out["data"]["seen"] == 1
    assert "change_id" not in out["data"]


@pytest.mark.asyncio
async def test_partial_and_skipped_are_read_from_result_not_data() -> None:
    out = await ActionsBridge(
        _ObjectAdapter(
            _Envelope(
                "ok",
                _Body(seen=1),
                data={"partial": True, "skipped": ["take-9"]},
            )
        )
    ).execute("look", {}, {})
    assert out["status"] == "ok"
    assert out["ok"] is True
    assert out["data"] == {"seen": 1}
    assert out["result"] == {"seen": 1}
