"""Мост r2.3: ok/data/cost, код ошибки, partial."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.wiring import ActionsBridge
from app.orchestrator.contracts import envelope_warnings


class _Inner:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

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
