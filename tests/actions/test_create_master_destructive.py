"""F4: prompt.create_master пишет общий файл и требует подтверждения."""

from __future__ import annotations

from app.services.action_layer.spec import ensure_builtin_actions, registry


def test_create_master_is_destructive_in_registry() -> None:
    ensure_builtin_actions()
    spec = registry.get("prompt.create_master")
    assert spec.risk == "destructive"
    assert spec.destructive is True
    assert spec.needs_approval is True
    assert spec.scope == "global"
    text = spec.destructive_effects({"step_code": "plan", "name": "fresh"})
    assert "ГЛОБАЛЬНО" in text
    assert "plan/fresh" in text
