"""Каталог builtin_group: флаги риска, схемы, оценка стоимости, защита от дублей."""
from __future__ import annotations

import pytest

from app.services.action_layer.builtin_group import register_group_actions
from app.services.action_layer.spec import ActionRegistry

EXPECTED = {
    "project.stop": "local_safe", "shot.get_menu": "read", "shot.edit_cell_voiceover": "local_safe",
    "shot.add_cell": "local_safe", "shot.edit_field": "local_safe", "group.list": "read",
    "group.get": "read", "group.shots_report": "read", "group.insert": "local_safe",
    "group.save_from_selection": "local_safe", "group.update": "local_safe",
    "group.delete": "destructive", "node.get_config": "read", "node.get_llm_route": "read",
    "node.set_config": "local_safe", "node.set_prompt_variant": "local_safe",
    "node.set_disabled": "local_safe", "node.run_step": "destructive",
    "node.reset_from_step": "destructive",
}


@pytest.fixture
def reg() -> ActionRegistry:
    r = ActionRegistry()
    register_group_actions(r)
    return r


def test_all_actions_registered_with_expected_tiers(reg) -> None:
    assert {s.id: s.risk for s in reg.list()} == EXPECTED
    with pytest.raises(Exception):
        register_group_actions(reg)  # дубликаты запрещены


def test_node_run_is_paid_destructive_job_with_stop_and_unknown_cost(reg) -> None:
    spec = reg.get("node.run_step")
    assert spec.spends_money and spec.destructive and not spec.reversible and not spec.idempotent
    assert spec.is_job and spec.stop is not None
    assert "убивает" in spec.destructive_effects({"node_key": "n_x"})
    pre = {p.id for p in spec.preconditions}
    assert {"not_managed", "project_idle", "no_montage_jobs", "node_runnable"} <= pre
    est = spec.estimate({"project_id": 1, "node_key": "n_excel_gpt_fw_shots"})
    assert est.known is False  # fail-closed: цена Vibecode не откалибрована
    free = spec.estimate({"project_id": 1, "node_key": "n_excel_gpt_fw_report"})
    assert free.known and free.high == 0.0


def test_only_node_run_spends_money_and_destructive_have_effects(reg) -> None:
    assert [s.id for s in reg.list() if s.spends_money] == ["node.run_step"]
    for s in reg.list():
        if s.destructive:
            assert s.destructive_effects and s.destructive_effects(
                {"group_id": "g", "node_key": "n", "step_code": "plan"})


def test_read_actions_are_idempotent_and_params_are_strict(reg) -> None:
    for s in reg.list(risk="read"):
        assert s.idempotent
    assert reg.validate_params("node.set_config", {"project_id": 1, "node_key": "n", "x": 1})
    assert reg.validate_params("node.set_config", {"project_id": 1, "node_key": "n", "role": "bad"})
    assert reg.validate_params("node.set_config", {"project_id": 1, "node_key": "n", "role": "review"}) == []
    assert reg.validate_params("shot.edit_field", {
        "project_id": 1, "frame_uuid": "u", "field": "id", "value": "x"})
    assert reg.validate_params("group.get", {"group_id": "../etc"})
    assert reg.validate_params("node.run_step", {"project_id": 1, "node_key": "a b"})
