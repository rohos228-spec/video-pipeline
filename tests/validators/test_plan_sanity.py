"""plan_sanity: whitelist, схемы, verify перед run_node, запрет удалений."""

from __future__ import annotations

import sys
import types

import pytest

from app.orchestrator.validators import VALIDATOR_CONTEXT_DRY_RUN, run
from app.orchestrator.validators.plan_sanity import FALLBACK_WHITELIST


def _verify(path: str = "app/services/x.py", node_key: str = "n_frames") -> dict:
    return {"action": "verify_code_state", "params": {"path": path, "node_key": node_key}}


def _run_node() -> dict:
    return {"action": "run_node", "params": {"project_id": 65, "node_key": "n_frames"}}


def test_verify_then_run_node_passes() -> None:
    result = run("plan_sanity", 65, {"plan": [_verify(), _run_node()], VALIDATOR_CONTEXT_DRY_RUN: True})
    assert result.passed is True
    assert result.blocking is False


def test_read_code_and_get_node_count_as_verify() -> None:
    plan = [
        {"action": "read_pipeline_code", "params": {"path": "app/services/x.py"}},
        {"action": "get_node", "params": {"node_key": "n_frames"}},
        _run_node(),
    ]
    assert run("plan_sanity", 1, {"plan": plan}).passed is True


def test_run_node_without_verify_blocks() -> None:
    result = run("plan_sanity", 1, {"plan": [_run_node()]})
    assert result.blocking is True
    assert result.details["errors"][0]["reason"] == "missing_verify_code_state"


def test_second_run_node_needs_its_own_verify() -> None:
    plan = [_verify(), _run_node(), _run_node()]
    result = run("plan_sanity", 1, {"plan": plan})
    assert result.passed is False
    assert result.details["errors"][0]["index"] == 2


def test_edit_files_rejected_by_fallback_whitelist() -> None:
    assert "edit_files" not in FALLBACK_WHITELIST
    result = run("plan_sanity", 1, {"plan": [{"action": "edit_files", "params": {"path": "a"}}]})
    assert result.blocking is True
    assert result.details["errors"][0]["reason"] == "not_in_whitelist"


def test_montage_delete_child_frame_is_blocked() -> None:
    """Без soft_delete одобрение ядра (risk=high, destructive) кадр не отпускает."""
    plan = [{"action": "montage.delete_child_frame", "params": {"frame_id": "f1"}}]
    result = run(
        "plan_sanity",
        1,
        {
            "plan": plan,
            "whitelist": ["montage.delete_child_frame"],
            "approval": _core_approval("montage.delete_child_frame"),
            "registry": _meta_registry({"montage.delete_child_frame": {}}),
        },
    )
    assert result.blocking is True
    assert result.details["errors"][0]["reason"] == "deletion_forbidden"


def _meta_registry(meta: dict) -> object:
    class Registry:
        def names(self) -> list[str]:
            return ["montage.delete_frame", "montage.delete_child_frame"]

        def metadata_of(self, name: str) -> dict:
            return dict(meta.get(name) or {})

    return Registry()


def _install_meta(monkeypatch: pytest.MonkeyPatch, meta: dict) -> None:
    module = types.ModuleType("app.orchestrator.actions")
    module.registry = _meta_registry(meta)
    monkeypatch.setitem(sys.modules, "app.orchestrator.actions", module)


def _core_approval(action: str) -> dict:
    """Форма одобрения WS3: change_id, action, risk, destructive, granted, cc_change_id."""
    return {
        "change_id": "chg-1",
        "action": action,
        "risk": "high",
        "destructive": True,
        "granted": True,
        "cc_change_id": "cc-1",
    }


@pytest.mark.parametrize("action", ["montage.delete_frame", "montage.delete_child_frame"])
def test_montage_delete_blocked_without_soft_delete_even_with_approval(
    action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_meta(monkeypatch, {action: {}})
    result = run(
        "plan_sanity",
        1,
        {
            "plan": [{"action": action, "params": {"frame_id": "f1"}}],
            "approval": _core_approval(action),
        },
    )
    assert result.blocking is True
    assert result.details["errors"][0]["reason"] == "deletion_forbidden"


@pytest.mark.parametrize("action", ["montage.delete_frame", "montage.delete_child_frame"])
def test_montage_delete_blocked_when_approval_names_another_action(
    action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Одобрение на другое действие не открывает этот кадр. Остальные поля валидны."""
    other = (
        "montage.delete_frame"
        if action == "montage.delete_child_frame"
        else "montage.delete_child_frame"
    )
    _install_meta(monkeypatch, {action: {"soft_delete": True}})
    approval = _core_approval(other)
    result = run(
        "plan_sanity",
        1,
        {
            "plan": [{"action": action, "params": {"frame_id": "f1"}}],
            "approval": approval,
        },
    )
    assert result.blocking is True
    assert result.details["errors"][0]["reason"] == "deletion_forbidden"


def test_montage_delete_blocked_when_risk_is_not_exactly_high(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """risk сравнивается как risk == \"high\": \"HIGH\" и \" high \" не проходят."""
    action = "montage.delete_frame"
    _install_meta(monkeypatch, {action: {"soft_delete": True}})
    for risk in ("HIGH", " high "):
        approval = _core_approval(action)
        approval["risk"] = risk
        result = run(
            "plan_sanity",
            1,
            {
                "plan": [{"action": action, "params": {"frame_id": "f1"}}],
                "approval": approval,
            },
        )
        assert result.blocking is True, risk
        assert result.details["errors"][0]["reason"] == "deletion_forbidden"


@pytest.mark.parametrize("action", ["montage.delete_frame", "montage.delete_child_frame"])
def test_montage_delete_blocked_when_granted_is_false(
    action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """granted=False блокирует, даже если имя, risk и destructive верные."""
    _install_meta(monkeypatch, {action: {"soft_delete": True}})
    approval = _core_approval(action)
    approval["granted"] = False
    result = run(
        "plan_sanity",
        1,
        {
            "plan": [{"action": action, "params": {"frame_id": "f1"}}],
            "approval": approval,
        },
    )
    assert result.blocking is True
    assert result.details["errors"][0]["reason"] == "deletion_forbidden"


@pytest.mark.parametrize("action", ["montage.delete_frame", "montage.delete_child_frame"])
def test_montage_delete_blocked_with_soft_delete_without_approval(
    action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_meta(monkeypatch, {action: {"soft_delete": True}})
    result = run(
        "plan_sanity",
        1,
        {"plan": [{"action": action, "params": {"frame_id": "f1"}}]},
    )
    assert result.blocking is True
    assert result.details["errors"][0]["reason"] == "deletion_forbidden"


@pytest.mark.parametrize("action", ["montage.delete_frame", "montage.delete_child_frame"])
def test_montage_delete_passes_with_soft_delete_and_approval(
    action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _install_meta(monkeypatch, {action: {"soft_delete": True}})
    result = run(
        "plan_sanity",
        1,
        {
            "plan": [{"action": action, "params": {"frame_id": "f1"}}],
            "approval": _core_approval(action),
        },
    )
    assert result.passed is True
    assert result.blocking is False


def test_deletion_action_blocked_even_inside_whitelist() -> None:
    plan = [{"action": "delete_frame", "params": {"id": "f1"}}]
    result = run("plan_sanity", 1, {"plan": plan, "whitelist": ["delete_frame", "run_node"]})
    assert result.details["errors"][0]["reason"] == "deletion_forbidden"


def test_deletion_hidden_in_ops_is_blocked() -> None:
    plan = [
        {
            "action": "run_validators",
            "params": {
                "project_id": 1,
                "ops": [{"op": "delete", "target": "frame", "id": "f1"}],
            },
        }
    ]
    result = run("plan_sanity", 1, {"plan": plan})
    assert result.details["errors"][0]["reason"] == "deletion_forbidden"


def test_schema_requires_path_and_section_anchor() -> None:
    missing_path = run(
        "plan_sanity",
        1,
        {"plan": [{"action": "read_pipeline_code", "params": {"max_bytes": True}}]},
    )
    reasons = {item["reason"] for item in missing_path.details["errors"]}
    assert "missing_param" in reasons
    assert "bad_type" in reasons
    section = run(
        "plan_sanity",
        1,
        {
            "plan": [
                {
                    "action": "edit_node_prompt",
                    "params": {
                        "node_key": "n_frames",
                        "patch_mode": "replace_section",
                        "content": "x",
                        "reason": "почему",
                    },
                }
            ]
        },
    )
    assert any(item["field"] == "section_anchor" for item in section.details["errors"])


def test_context_cannot_expand_registry() -> None:
    """context['whitelist'] / context['registry'] сужают реестр и не добавляют имён."""

    attack = run(
        "plan_sanity",
        1,
        {"plan": [{"action": "edit_files", "params": {"path": "a"}}], "whitelist": ["edit_files"]},
    )
    assert attack.passed is False
    assert attack.blocking is True
    assert attack.details["errors"][0]["reason"] == "not_in_whitelist"

    mixed = run(
        "plan_sanity",
        1,
        {
            "plan": [
                {"action": "get_node", "params": {"node_key": "n_a"}},
                {"action": "edit_files", "params": {"path": "a"}},
                {"action": "list_nodes"},
            ],
            "whitelist": ["get_node", "edit_files"],
        },
    )
    assert mixed.passed is False
    by_action = {item["action"]: item["reason"] for item in mixed.details["errors"]}
    assert "get_node" not in by_action
    assert by_action["edit_files"] == "not_in_whitelist"
    assert by_action["list_nodes"] == "not_in_whitelist"

    class OnlyNode:
        def names(self) -> list[str]:
            return ["get_node", "edit_files"]

    kept = run(
        "plan_sanity",
        1,
        {"registry": OnlyNode(), "plan": [{"action": "get_node", "params": {"node_key": "n_a"}}]},
    )
    assert kept.passed is True
    expanded = run("plan_sanity", 1, {"registry": OnlyNode(), "plan": [{"action": "edit_files"}]})
    assert expanded.details["errors"][0]["reason"] == "not_in_whitelist"
    narrowed = run("plan_sanity", 1, {"registry": OnlyNode(), "plan": [{"action": "list_nodes"}]})
    assert narrowed.details["errors"][0]["reason"] == "not_in_whitelist"


def test_context_whitelist_and_object_step() -> None:
    class Step:
        action = "get_node"
        params = {"node_key": "n_frames"}

    result = run("plan_sanity", 1, {"plan": [Step()], "whitelist": ["get_node"]})
    assert result.passed is True


def test_plan_shape_errors() -> None:
    assert run("plan_sanity", 1, {}).details["reason"] == "no_plan"
    bad = run("plan_sanity", 1, {"plan": {"action": "get_node"}})
    assert bad.details["errors"][0]["reason"] == "plan_not_list"
    empty_action = run("plan_sanity", 1, {"plan": [{"params": {}}]})
    assert empty_action.details["errors"][0]["reason"] == "missing_action"


def test_registry_names_come_after_ensure_builtin_actions(monkeypatch) -> None:
    """Имена читаются только после ensure_builtin_actions()."""
    module = types.ModuleType("app.orchestrator.actions")
    ready = {"ok": False}

    class Registry:
        def names(self) -> list[str]:
            return ["custom_builtin_action"] if ready["ok"] else []

    def ensure_builtin_actions() -> None:
        ready["ok"] = True

    module.registry = Registry()
    module.ensure_builtin_actions = ensure_builtin_actions
    monkeypatch.setitem(sys.modules, "app.orchestrator.actions", module)
    result = run(
        "plan_sanity",
        1,
        {"plan": [{"action": "custom_builtin_action", "params": {}}]},
    )
    assert result.passed is True


def test_registry_names_and_validate_params(monkeypatch) -> None:
    module = types.ModuleType("app.orchestrator.actions")

    class Registry:
        def names(self) -> list[str]:
            return ["get_node", "run_node", "verify_code_state"]

        def validate_params(self, action: str, params: dict) -> list[str]:
            if action == "get_node" and "node_key" not in params:
                return ["node_key required"]
            if action == "missing":
                raise KeyError(action)
            return []

    module.registry = Registry()
    monkeypatch.setitem(sys.modules, "app.orchestrator.actions", module)
    ok = run(
        "plan_sanity",
        1,
        {"plan": [{"action": "get_node", "params": {"node_key": "n_frames"}}]},
    )
    assert ok.passed is True
    bad = run("plan_sanity", 1, {"plan": [{"action": "get_node", "params": {}}]})
    assert bad.details["errors"][0]["message"] == "node_key required"
    monkeypatch.undo()


def test_registry_dict_and_validate_exception(monkeypatch) -> None:
    module = types.ModuleType("app.services.action_layer.spec")

    class Registry:
        _actions = {"get_shots": object()}

        def validate_params(self, action: str, params: dict) -> list[str]:
            raise RuntimeError("schema boom")

    module.registry = lambda: Registry()
    monkeypatch.setitem(sys.modules, "app.services.action_layer.spec", module)
    result = run("plan_sanity", 1, {"plan": [{"action": "get_shots", "params": {}}]})
    assert result.details["errors"][0]["message"] == "schema boom"


def test_dry_run_does_not_mutate_plan() -> None:
    plan = [_verify(), _run_node()]
    context = {"plan": plan, VALIDATOR_CONTEXT_DRY_RUN: True}
    run("plan_sanity", 1, context)
    assert context["plan"][0]["action"] == "verify_code_state"
    assert len(plan) == 2
