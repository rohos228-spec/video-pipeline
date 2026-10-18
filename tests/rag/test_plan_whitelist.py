"""plan_sanity принимает только действия из реестра."""

from __future__ import annotations

from evals.orchestrator.checks import ACTION_WHITELIST, plan_sanity
from evals.orchestrator.mock_actions import ALLOWED_ACTIONS
from evals.orchestrator.run_evals import run_contour_a


def test_actions_outside_the_registry_are_rejected() -> None:
    assert ALLOWED_ACTIONS is ACTION_WHITELIST
    for name in ("fix_bugs", "diagnose_only", "not_in_registry", "edit_files", "subprocess"):
        result = plan_sanity({"steps": [{"action": name}]})
        assert result["pass"] is False
        assert result["blocking"] is True
        assert result["reason"] == "action_not_in_whitelist"
        assert name in result["actions"]
    allowed = plan_sanity(
        {
            "steps": [
                {"action": "read_pipeline_code", "params": {"path": "app/services/node_groups.py"}},
                {"action": "get_node", "params": {"node_key": "n_excel_gpt_fw_shots"}},
                {"action": "run_node", "params": {"node_key": "n_excel_gpt_fw_shots"}},
            ]
        }
    )
    assert allowed["pass"] is True


def test_e8_covers_fix_bugs_diagnose_only_and_unknown() -> None:
    checks = run_contour_a()["by_id"]["E8"]["details"]["checks"]
    assert checks["fix_bugs_rejected"] is True
    assert checks["diagnose_only_rejected"] is True
    assert checks["unknown_action_rejected"] is True
