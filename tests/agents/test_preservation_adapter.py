"""PreservationRejected из мягкого импорта WS1 становится блокирующим результатом."""

from __future__ import annotations

import sys
import types

import pytest

from app.orchestrator.agents.wiring import FunctionValidators
from app.orchestrator.contracts import ValidatorResult


class PreservationRejected(Exception):
    def __init__(self, violations: list[dict[str, str]]) -> None:
        self.violations = list(violations)
        codes = [
            str(item["code"])
            for item in self.violations
            if isinstance(item, dict) and item.get("code")
        ]
        super().__init__("сохранность: " + ",".join(codes))


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


def test_preservation_contract_is_keyword_reject() -> None:
    """Контракт подписи: mode keyword-only, по умолчанию reject, ядро шлёт reject."""
    import ast
    import inspect
    import re
    import textwrap

    from app.orchestrator.agents.wiring import FunctionValidators, _preservation_symbols

    text = _preservation_symbols.__doc__ or ""
    assert "run_preservation_validators(before, after, ops=None, *, mode)" in text
    assert "keyword-only" in text
    assert 'по умолчанию ``"reject"``' in text
    assert 'mode="reject"' in text
    assert "PreservationRejected" in text
    assert re.search(r"\b[0-9a-f]{7,40}\b", text) is None
    source = textwrap.dedent(inspect.getsource(FunctionValidators.run))
    assert 'mode="reject"' in source
    tree = ast.parse(source)
    modes = [
        kw.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        for kw in node.keywords
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant)
    ]
    assert modes == ["reject"]


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


@pytest.mark.asyncio
async def test_unknown_preservation_code_fails_closed_without_a_write(token: str) -> None:
    """Любой код PreservationRejected, включая неизвестный, отклоняет шаг.

    Запись не вызывается. Код попадает в событие validator_result.
    """
    from app.orchestrator.agents.runtime import build_runtime, set_runtime
    from app.orchestrator.contracts import PlanStep, TaskStatus
    from tests.agents.mocks import FixedPlanner, MockActions, ScriptedRouter

    del token
    previous = _install()
    record = {"text": "было"}

    def _run(name: str, project_id: int, context: dict) -> dict:
        del project_id, context
        if name == "no_deletions":
            raise PreservationRejected(
                [{"code": "marked_shot_removed", "severity": "error", "id": "s1"}]
            )
        return {"pass": True}

    actions = MockActions()

    async def _write(name: str, params: dict, ctx: dict) -> None:
        del name, params, ctx
        record["text"] = "стало"

    actions.on_execute = _write
    runtime = build_runtime(
        actions=actions,
        validators=FunctionValidators(_run),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="noop", mutating=True, expected="ok")]),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("pres-unknown", "проверка", project_id=13)
        await runtime.supervisor.wait(task_id, timeout=5)
        row = runtime.tasks.get(task_id)
        assert row["status"] == TaskStatus.failed.value
        assert row["error"] == "validator_blocked"
        assert actions.calls == []
        assert record["text"] == "было"
        events = [
            event
            for event in runtime.bus.replay("pres-unknown", 0)
            if event.type == "validator_result"
        ]
        assert events
        assert any("marked_shot_removed" in str(event.payload.get("details")) for event in events)
        failed = [event for event in runtime.bus.replay("pres-unknown", 0) if event.type == "task_failed"]
        assert failed
        assert failed[-1].payload["error"] == "validator_blocked"
    finally:
        set_runtime(None)
        _restore(previous)
