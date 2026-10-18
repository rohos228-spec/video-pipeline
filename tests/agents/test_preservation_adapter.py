"""PreservationRejected из мягкого импорта WS1 становится блокирующим результатом."""

from __future__ import annotations

import sys
import types

from app.orchestrator.agents.wiring import FunctionValidators
from app.orchestrator.contracts import ValidatorResult


class PreservationRejected(Exception):
    def __init__(self, violations: list[dict[str, str]]) -> None:
        self.violations = list(violations)
        super().__init__("сохранность: frame_deleted")


def _install() -> dict | None:
    previous = sys.modules.get("app.orchestrator.validators.preservation")
    module = types.ModuleType("app.orchestrator.validators.preservation")
    module.PreservationRejected = PreservationRejected

    def run_preservation_validators(before, after, ops=None, *, mode="reject"):
        del after, ops, mode
        assert before["scenes"][0]["id"] == "s1"
        raise PreservationRejected([{"code": "frame_deleted", "severity": "error"}])

    module.run_preservation_validators = run_preservation_validators
    sys.modules["app.orchestrator.validators.preservation"] = module
    return previous


def _restore(previous: dict | None) -> None:
    if previous is None:
        sys.modules.pop("app.orchestrator.validators.preservation", None)
    else:
        sys.modules["app.orchestrator.validators.preservation"] = previous


def test_preservation_rejected_is_blocking_and_does_not_touch_data() -> None:
    previous = _install()
    before = {"scenes": [{"id": "s1", "кадры": [{"id": "f1"}]}]}
    snapshot = {"scenes": [{"id": "s1", "кадры": [{"id": "f1"}]}]}
    try:
        def _raise(name: str, project_id: int, context: dict) -> dict:
            del name, project_id, context
            raise PreservationRejected([{"code": "frame_deleted"}])

        blocked = FunctionValidators(_raise).run("no_deletions", 13, {"before": before})
        assert isinstance(blocked, ValidatorResult)
        assert blocked.passed is False
        assert blocked.blocking is True
        assert before == snapshot

        def _ok(name: str, project_id: int, context: dict) -> dict:
            del name, project_id, context
            return {"pass": True}

        preserved = FunctionValidators(_ok).run(
            "preservation",
            13,
            {"before": before, "after": before, "ops": []},
        )
        assert isinstance(preserved, ValidatorResult)
        assert preserved.blocking is True
        assert preserved.passed is False
        assert before == snapshot
    finally:
        _restore(previous)
