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
    module.seen = []

    def run_preservation_validators(before, after, ops=None, *, mode):
        module.seen.append(mode)
        assert before["scenes"][0]["id"] == "s1"
        assert after["scenes"][0]["id"] == "s1"
        del ops
        raise PreservationRejected([{"code": "frame_deleted", "severity": "error"}])

    module.run_preservation_validators = run_preservation_validators
    sys.modules["app.orchestrator.validators.preservation"] = module
    return previous


def _restore(previous: dict | None) -> None:
    if previous is None:
        sys.modules.pop("app.orchestrator.validators.preservation", None)
    else:
        sys.modules["app.orchestrator.validators.preservation"] = previous


def test_preservation_pin_cites_ws1_head() -> None:
    """Пин WS1 #379 — HEAD 76198ecd, mode keyword-only и по умолчанию reject."""
    import inspect

    from app.orchestrator.agents.wiring import FunctionValidators, _preservation_symbols

    text = _preservation_symbols.__doc__ or ""
    assert "76198ecd" in text
    assert "9820d123" not in text
    assert "run_preservation_validators(before, after, ops=None, *, mode)" in text
    assert 'mode="reject"' in text
    assert "PreservationRejected" in text
    assert 'mode="reject"' in inspect.getsource(FunctionValidators.run)


def test_preservation_rejected_is_blocking_and_does_not_touch_data() -> None:
    previous = _install()
    before = {"scenes": [{"id": "s1", "кадры": [{"id": "f1"}]}]}
    after = {"scenes": [{"id": "s1", "кадры": [{"id": "f1"}]}]}
    before_snapshot = {"scenes": [{"id": "s1", "кадры": [{"id": "f1"}]}]}
    after_snapshot = {"scenes": [{"id": "s1", "кадры": [{"id": "f1"}]}]}
    try:
        def _raise(name: str, project_id: int, context: dict) -> dict:
            del name, project_id, context
            raise PreservationRejected([{"code": "frame_deleted"}])

        blocked = FunctionValidators(_raise).run("no_deletions", 13, {"before": before, "after": after})
        assert isinstance(blocked, ValidatorResult)
        assert blocked.passed is False
        assert blocked.blocking is True
        assert before == before_snapshot
        assert after == after_snapshot

        def _ok(name: str, project_id: int, context: dict) -> dict:
            del name, project_id, context
            return {"pass": True}

        preserved = FunctionValidators(_ok).run(
            "preservation",
            13,
            {"before": before, "after": after, "ops": [], "mode": "warn"},
        )
        assert isinstance(preserved, ValidatorResult)
        assert preserved.blocking is True
        assert preserved.passed is False
        assert before == before_snapshot
        assert after == after_snapshot
        module = sys.modules["app.orchestrator.validators.preservation"]
        assert module.seen == ["reject"]
    finally:
        _restore(previous)


def test_payload_mode_does_not_weaken_preservation() -> None:
    """mode из payload не ослабляет серверный reject."""
    previous = _install()
    before = {"scenes": [{"id": "s1", "кадры": [{"id": "f1"}]}]}
    after = {"scenes": [{"id": "s1", "кадры": [{"id": "f1"}]}]}
    try:
        def _ok(name: str, project_id: int, context: dict) -> dict:
            del name, project_id, context
            return {"pass": True}

        FunctionValidators(_ok).run(
            "preservation",
            13,
            {"before": before, "after": after, "ops": [], "mode": "warn"},
        )
        module = sys.modules["app.orchestrator.validators.preservation"]
        assert module.seen == ["reject"]
    finally:
        _restore(previous)
