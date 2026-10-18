"""destructive=True остаётся deletion_blocked, кроме закрытого списка без медиа."""

from __future__ import annotations

from app.orchestrator.contracts import PlanStep, action_guard, step_is_deletion


class _Registry:
    def __init__(self, table: dict[str, dict[str, object]]) -> None:
        self.table = table

    def metadata_of(self, name: str) -> dict[str, object]:
        if name not in self.table:
            raise KeyError(name)
        return self.table[name]


def _meta(destructive: bool) -> dict[str, object]:
    return {"destructive": destructive, "read_only": False, "paid": False, "risk": "medium"}


def test_only_closed_destructive_names_are_approvable() -> None:
    registry = _Registry(
        {
            "revert_node_to_git": _meta(True),
            "prompt.retire_master": _meta(True),
            "merge_frames": _meta(True),
            "look": {"destructive": False, "read_only": True, "paid": False, "risk": "none"},
        }
    )
    for name in ("revert_node_to_git", "prompt.retire_master"):
        assert action_guard(name, registry) is None
        assert step_is_deletion(PlanStep(action=name), registry) is False
    assert action_guard("merge_frames", registry) == "name"
    assert step_is_deletion(PlanStep(action="merge_frames"), registry) is True
    assert action_guard("delete_child_frame", registry) == "name"
    assert step_is_deletion(PlanStep(action="look"), registry) is False
