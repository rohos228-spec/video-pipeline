"""Имена PLAN §6.1–6.2 против того, что ядро зовёт, и реестра ws2-r2.2.

Снимок флагов — #378 ``a17ff283``. Пакет WS2 не импортируется.
Дыра: аналога в реестре нет, имя остаётся paid и destructive.
"""

from __future__ import annotations

from app.orchestrator.agents.critic import _critic_system
from app.orchestrator.agents.planner import _system_prompt
from app.orchestrator.contracts import (
    PLAN_ACTION_MAP,
    RUN_NODE_ACTIONS,
    metadata_call_gate,
    plan_action_metadata,
    resolve_plan_action,
)


def _core_mentions() -> str:
    return "\n".join(
        (
            _system_prompt([]),
            _critic_system([]),
            " ".join(sorted(RUN_NODE_ACTIONS)),
        )
    )


class _R22Metadata:
    """``metadata_of`` как в r2.2: неизвестное имя — paid и destructive, без KeyError."""

    def metadata_of(self, name: str) -> dict[str, object]:
        flags = plan_action_metadata(name)
        if flags["resolved"]:
            return {
                "name": flags["resolved"],
                "paid": flags["paid"],
                "destructive": flags["destructive"],
                "risk": flags["risk"],
                "read_only": False,
            }
        return {
            "name": name,
            "paid": True,
            "destructive": True,
            "risk": "high",
            "read_only": False,
        }


def _assert_hole(name: str) -> None:
    assert name not in _core_mentions()
    assert resolve_plan_action(name) is None
    flags = plan_action_metadata(name)
    assert flags["hole"] is True
    assert flags["paid"] is True
    assert flags["destructive"] is True
    meta = _R22Metadata().metadata_of(name)
    assert meta["paid"] is True
    assert meta["destructive"] is True
    assert metadata_call_gate(name, _R22Metadata()) == "destructive"


def test_get_project_state_is_a_hole() -> None:
    _assert_hole("get_project_state")


def test_list_nodes_is_a_hole() -> None:
    _assert_hole("list_nodes")


def test_get_node_is_called_but_has_no_r22_name() -> None:
    """Планировщик пишет литерал ``get_node``. В r2.2 такого действия нет."""
    assert "get_node" in _system_prompt([])
    assert resolve_plan_action("get_node") is None
    flags = plan_action_metadata("get_node")
    assert flags["hole"] is True
    assert flags["paid"] is True
    assert flags["destructive"] is True
    meta = _R22Metadata().metadata_of("get_node")
    assert meta["paid"] is True and meta["destructive"] is True
    assert metadata_call_gate("get_node", _R22Metadata()) == "destructive"


def test_get_scenes_is_a_hole() -> None:
    _assert_hole("get_scenes")


def test_get_shots_is_a_hole() -> None:
    _assert_hole("get_shots")


def test_get_run_logs_is_a_hole() -> None:
    _assert_hole("get_run_logs")


def test_get_spend_report_is_a_hole() -> None:
    _assert_hole("get_spend_report")


def test_hole_names_are_exactly_the_unmapped_plan_reads() -> None:
    holes = {name for name, target in PLAN_ACTION_MAP.items() if target is None}
    assert holes == {
        "get_project_state",
        "list_nodes",
        "get_node",
        "get_scenes",
        "get_shots",
        "get_run_logs",
        "get_spend_report",
    }
