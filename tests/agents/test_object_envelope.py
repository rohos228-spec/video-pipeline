"""Объектный конверт WS2: status/result и отдельный cc_change_id."""

from __future__ import annotations

import pytest

from app.orchestrator.agents.wiring import ActionsBridge


class _Body:
    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)

    def model_dump(self) -> dict[str, object]:
        return dict(self.__dict__)


class _Envelope:
    def __init__(self, status: str, result: _Body, error: str | None = None) -> None:
        self.status = status
        self.result = result
        self.error = error
        self.warnings: list[str] = []


class _ObjectActions:
    def metadata_of(self, name: str) -> dict[str, object]:
        return {"name": name, "risk": "none", "read_only": True, "destructive": False, "paid": False}

    def metadata_registry(self) -> dict[str, dict[str, object]]:
        return {"look": self.metadata_of("look")}

    async def execute(self, name: str, params: dict, task_ctx: dict) -> _Envelope:
        del name, params, task_ctx
        envelope = _Envelope("ok", _Body(seen=1, change_id="orch-appr-7-1"))
        envelope.cc_change_id = "cc-e2e-1"
        return envelope


@pytest.mark.asyncio
async def test_object_envelope_copies_cc_change_id() -> None:
    out = await ActionsBridge(_ObjectActions()).execute("look", {}, {})
    assert out["ok"] is True
    assert out["status"] == "ok"
    assert out["change_id"] is None
    assert out["cc_change_id"] == "cc-e2e-1"
    assert out["data"]["seen"] == 1
    assert out["data"].get("change_id") in (None,)
