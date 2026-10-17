"""Блокер ревью #350: детектор удаления и проверка пути по компонентам."""

from __future__ import annotations

import os

import pytest

from app.orchestrator.agents.runtime import build_runtime, set_runtime
from app.orchestrator.contracts import (
    ActionMetadata,
    PlanStep,
    TaskStatus,
    action_guard,
    is_deletion_action,
    is_named_deletion,
)
from app.orchestrator.path_guard import path_is_within
from tests.agents.mocks import FixedPlanner, MockActions, MockValidators, ScriptedRouter

_NAMES = [
    "scene.delete",
    "frames.delete",
    "shots.remove",
    "montage.remove_shot",
    "delete_shot",
    "scenes.remove",
    "kadr.erase",
    "kadry.wipe",
    "os.remove",
    "os.unlink",
    "os.rmdir",
    "os.removedirs",
    "shutil.rmtree",
    "Path.unlink",
    "Path.rmdir",
    "clear",
    "wipe",
    "truncate",
    "prune",
    "reset",
    "destroy",
    "remove_watermark",
]


@pytest.mark.parametrize("action", _NAMES)
def test_common_deletion_names_are_blocked(action: str) -> None:
    assert is_deletion_action(action)


def test_dropdown_is_not_a_deletion_segment() -> None:
    assert not is_deletion_action("dropdown")
    assert not is_deletion_action("look")


def test_metadata_does_not_waive_scene_or_filesystem_deletion() -> None:
    class _Registry:
        def metadata_of(self, action_name: str) -> ActionMetadata | None:
            if action_name in {"scene.delete", "os.remove", "remove_watermark"}:
                return ActionMetadata(read_only=True, destructive=False, risk="none")
            return None

    registry = _Registry()
    assert action_guard("scene.delete", registry) == "name"
    assert action_guard("os.remove", registry) == "name"
    assert is_named_deletion("os.remove")
    assert action_guard("remove_watermark", registry) is None


@pytest.mark.asyncio
async def test_known_high_deletion_name_is_not_granted(token: str) -> None:
    del token
    actions = MockActions(risks={"scene.delete": "high"})
    runtime = build_runtime(
        actions=actions,
        validators=MockValidators(),
        router=ScriptedRouter(),
        planner=FixedPlanner([PlanStep(action="scene.delete", mutating=True)]),
    )
    set_runtime(runtime)
    try:
        task_id = await runtime.supervisor.start_task("del-known", "удали сцену")
        await runtime.supervisor.wait(task_id, timeout=5)
        assert actions.calls == []
        assert runtime.tasks.get(task_id)["status"] == TaskStatus.failed.value
        assert runtime.tasks.get(task_id)["error"] == "deletion_blocked"
    finally:
        set_runtime(None)


def test_path_prefix_lookalike_is_outside(tmp_path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    sibling = tmp_path / "allowed_evil"
    sibling.mkdir()
    inside = root / "frame.png"
    inside.write_text("x", encoding="utf-8")
    assert str(sibling).startswith(str(root))
    assert path_is_within(root, inside)
    assert path_is_within(root, root)
    assert not path_is_within(root, sibling)


def test_symlink_outside_root_is_rejected(tmp_path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "scene.png"
    secret.write_text("x", encoding="utf-8")
    link = root / "alias"
    os.symlink(secret, link)
    assert not path_is_within(root, link)
