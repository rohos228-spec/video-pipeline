"""Реестр действий: регистрация, защита от дублей, валидация параметров."""
from __future__ import annotations

import pytest

from app.services.action_layer import schema as schema_mod
from app.services.action_layer.spec import (
    ActionRegistry, ActionRegistryError, ActionSpec, ensure_builtin_actions, registry)
from tests.action_layer_support import make_spec


async def _noop(ctx, params):
    return {}


def test_register_get_list_and_duplicate_protection() -> None:
    reg = ActionRegistry()
    spec = make_spec("area.read_one", _noop)
    reg.register(spec)
    assert reg.get("area.read_one") is spec
    assert [s.id for s in reg.list()] == ["area.read_one"]
    with pytest.raises(ActionRegistryError, match="duplicate"):
        reg.register(make_spec("area.read_one", _noop))
    with pytest.raises(KeyError):
        reg.get("area.missing")


def test_register_many_is_all_or_nothing() -> None:
    reg = ActionRegistry()
    reg.register(make_spec("a.one", _noop))
    with pytest.raises(ActionRegistryError):
        reg.register_many([make_spec("a.two", _noop), make_spec("a.one", _noop)])
    assert "a.two" not in reg and len(reg) == 1


@pytest.mark.parametrize("bad_id", ["", "noarea", "Area.Name", "a.b-c", "a..b"])
def test_invalid_ids_rejected(bad_id: str) -> None:
    with pytest.raises(ActionRegistryError):
        ActionRegistry().register(make_spec(bad_id, _noop))


def test_risk_flags_must_be_consistent() -> None:
    reg = ActionRegistry()
    with pytest.raises(ActionRegistryError, match="estimator"):
        reg.register(ActionSpec(id="a.paid", title_ru="t", description_ru="d",
                                params_schema={"type": "object", "additionalProperties": False},
                                executor=_noop, risk="paid", spends_money=True))
    with pytest.raises(ActionRegistryError):  # paid без spends_money
        reg.register(make_spec("a.p2", _noop, risk="paid"))
    with pytest.raises(ActionRegistryError):  # spends при local_safe
        reg.register(make_spec("a.p3", _noop, risk="local_safe", spends=True, cost=(1, 2)))
    with pytest.raises(ActionRegistryError):  # destructive-флаг без tier
        reg.register(make_spec("a.p4", _noop, risk="local_safe", destructive=True))
    with pytest.raises(ActionRegistryError):
        reg.register(make_spec("a.p5", _noop, risk="bogus"))


def test_paid_and_destructive_in_one_action_is_allowed() -> None:
    reg = ActionRegistry()
    spec = reg.register(make_spec("a.both", _noop, risk="destructive", spends=True,
                                  destructive=True, cost=(1, 2), reversible=False))
    assert spec.spends_money and spec.destructive and spec.needs_approval


def test_executor_must_be_async() -> None:
    def sync_fn(ctx, params):
        return {}

    with pytest.raises(ActionRegistryError, match="async"):
        ActionRegistry().register(make_spec("a.sync", sync_fn))


def test_params_schema_must_be_strict_object_and_known_keywords() -> None:
    reg = ActionRegistry()
    with pytest.raises(ActionRegistryError, match="additionalProperties"):
        reg.register(make_spec("a.loose", _noop, schema={"type": "object"}))
    with pytest.raises(ActionRegistryError, match="unsupported keyword"):
        reg.register(make_spec("a.fancy", _noop, schema={
            "type": "object", "additionalProperties": False, "oneOf": []}))


def test_validate_params_rules() -> None:
    reg = ActionRegistry()
    reg.register(make_spec("a.v", _noop, schema={
        "type": "object", "required": ["project_id", "mode"], "additionalProperties": False,
        "properties": {
            "project_id": {"type": "integer", "minimum": 1},
            "mode": {"type": "string", "enum": ["x", "y"]},
            "items": {"type": "array", "maxItems": 2, "items": {"type": "string", "maxLength": 3}},
        }}))
    assert reg.validate_params("a.v", {"project_id": 1, "mode": "x"}) == []
    assert reg.validate_params("a.v", {"project_id": 0, "mode": "x"})
    assert reg.validate_params("a.v", {"project_id": True, "mode": "x"})  # bool не int
    assert reg.validate_params("a.v", {"project_id": 1, "mode": "z"})
    assert reg.validate_params("a.v", {"project_id": 1})  # нет mode
    assert reg.validate_params("a.v", {"project_id": 1, "mode": "x", "extra": 1})
    assert reg.validate_params("a.v", {"project_id": 1, "mode": "x", "items": ["a", "b", "c"]})
    assert reg.validate_params("a.v", {"project_id": 1, "mode": "x", "items": ["long!"]})
    assert reg.validate_params("a.v", "nope")


def test_schema_checker_flags_bad_schema() -> None:
    assert schema_mod.check_schema({"type": "wat"})
    assert schema_mod.check_schema({"type": "object", "required": "x"})
    assert schema_mod.check_schema({"type": "string", "pattern": "("})


def test_external_module_can_register_into_global_registry() -> None:
    ensure_builtin_actions()
    before = len(registry)
    spec = make_spec("pkg.demo_node.run", _noop)
    registry.register(spec)
    try:
        assert registry.get("pkg.demo_node.run") is spec
        assert len(registry.list(prefix="pkg.")) == 1
    finally:
        registry.unregister("pkg.demo_node.run")
    assert len(registry) == before


def test_builtin_montage_first_slice_is_still_present_and_safe() -> None:
    ensure_builtin_actions()
    ensure_builtin_actions()
    first = ("montage.get_board", "montage.apply_status", "montage.montage_status", "montage.recover_status",
             "montage.set_voiceover", "montage.set_coverage", "montage.queue_ops", "montage.swap_shots")
    specs = [registry.get(i) for i in first]
    assert all(s is not None for s in specs)
    assert {s.risk for s in specs} <= {"read", "local_safe"}
    assert not any(s.spends_money or s.destructive for s in specs)
    assert sum(1 for s in specs if s.risk == "read") == 4