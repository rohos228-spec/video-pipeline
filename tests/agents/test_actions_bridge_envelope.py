"""ActionsBridge пропускает конверт r2.2 и срезает orch-appr внутри result."""

from __future__ import annotations

import sys
import types

import pytest

from app.orchestrator.agents.wiring import ActionsBridge, load_actions


class _Inner:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def execute(self, name: str, params: dict, task_ctx: dict) -> dict:
        del name, params, task_ctx
        return self.payload


@pytest.mark.asyncio
async def test_bridge_keeps_envelope_and_strips_nested_approval_id() -> None:
    payload = {
        "status": "approval_required",
        "result": {
            "change_id": "orch-appr-7-1",
            "snapshot_id": "snap-13-6",
            "skipped": ["n_other"],
            "partial": True,
        },
        "warnings": ["schema has no change_id"],
        "error": None,
    }
    out = await ActionsBridge(_Inner(payload)).execute("read_pipeline_code", {}, {})
    assert isinstance(out, dict)
    assert out["status"] == "approval_required"
    assert out["warnings"] == ["schema has no change_id"]
    assert out["result"]["snapshot_id"] == "snap-13-6"
    assert out["result"]["skipped"] == ["n_other"]
    assert out["result"]["partial"] is True
    assert out["result"]["change_id"] is None

    failed = await ActionsBridge(_Inner({"status": "failed", "error": "no", "result": {}})).execute("x", {}, {})
    assert failed["status"] == "failed"
    assert failed["error"] == "no"


@pytest.mark.asyncio
async def test_load_actions_bridge_strips_orch_appr() -> None:
    module = types.ModuleType("app.orchestrator.actions")

    async def execute(name: str, params: dict, task_ctx: dict) -> dict:
        del name, params, task_ctx
        return {"status": "ok", "result": {"change_id": "orch-appr-2-3", "snapshot_id": "snap"}}

    module.execute = execute
    previous = sys.modules.get("app.orchestrator.actions")
    sys.modules["app.orchestrator.actions"] = module
    try:
        bridge, degraded = load_actions()
        assert degraded is None
        out = await bridge.execute("revert_node_to_git", {}, {})
        assert out["status"] == "ok"
        assert out["result"]["snapshot_id"] == "snap"
        assert out["result"]["change_id"] is None
    finally:
        if previous is None:
            sys.modules.pop("app.orchestrator.actions", None)
        else:
            sys.modules["app.orchestrator.actions"] = previous
