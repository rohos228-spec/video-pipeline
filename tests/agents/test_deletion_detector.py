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
    params_request_deletion,
    step_is_deletion,
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
    "character.delete",
    "hero.remove",
    "vo.wipe",
    "voiceover.clear",
    "kadr.truncate",
    "os.remove",
    "clear",
    "wipe",
    "truncate",
    "prune",
    "reset",
    "destroy",
    "rename",
    "replace",
    "overwrite",
    "remove_watermark",
]

_PARAMS = [
    {"sql": "DELETE FROM scenes WHERE id = 1"},
    {"query": "DROP TABLE vo"},
    {"method": "DELETE"},
    {"http_method": "delete"},
    {"path": "replace frame"},
]


@pytest.mark.parametrize("action", _NAMES)
def test_common_deletion_names_are_blocked(action: str) -> None:
    assert is_deletion_action(action)


@pytest.mark.parametrize("params", _PARAMS)
def test_sql_and_http_delete_in_params_are_blocked(params: dict) -> None:
    assert params_request_deletion(params)
    registry = MockActions()
    step = PlanStep(action="look", params=params)
    assert step_is_deletion(step, registry)
    assert not step_is_deletion(PlanStep(action="look"), registry)


@pytest.mark.parametrize(
    "params",
    [
        {"method": None},
        {"method": 1},
        {"method": True},
        {"method": {"ref": "request.method"}},
        {"http_method": ["GET"]},
        {"sql": None},
        {"query": {"computed": True}},
        {"statement": 5},
    ],
)
def test_non_literal_http_or_sql_is_blocked(params: dict) -> None:
    step = PlanStep(action="look", params=params)
    assert params_request_deletion(params)
    assert step_is_deletion(step, MockActions())


@pytest.mark.parametrize(
    "params",
    [
        {"method": "{verb}"},
        {"http_method": "${method}"},
        {"sql": "`query`"},
        {"query": "{sql}"},
    ],
)
def test_computed_http_or_sql_is_blocked(params: dict) -> None:
    assert params_request_deletion(params)
    assert step_is_deletion(PlanStep(action="look", params=params), MockActions())


@pytest.mark.parametrize("params", [{"method": "CUSTOM"}, {"verb": "NOPE"}, {"http_method": ""}])
def test_unknown_http_method_is_blocked(params: dict) -> None:
    assert params_request_deletion(params)
    assert step_is_deletion(PlanStep(action="look", params=params), MockActions())
    assert not params_request_deletion({"method": "GET"})
    assert not params_request_deletion({"method": "POST"})
    assert not params_request_deletion({"sql": "SELECT 1"})


@pytest.mark.parametrize("params", [{"sql": "TRUNCATE TABLE frames"}, {"query": "truncate vo"}])
def test_sql_truncate_is_blocked(params: dict) -> None:
    assert params_request_deletion(params)
    assert step_is_deletion(PlanStep(action="look", params=params), MockActions())


@pytest.mark.parametrize(
    "params",
    [
        {"sql": "cursor.executemany(stmt, rows)"},
        {"statement": "executemany"},
    ],
)
def test_sql_executemany_is_blocked(params: dict) -> None:
    assert params_request_deletion(params)
    assert step_is_deletion(PlanStep(action="look", params=params), MockActions())


@pytest.mark.parametrize(
    "action",
    ["montage.delete_frame", "group.delete", "prompt.delete_master", "delete_child_frame"],
)
def test_r2_delete_actions_ignore_safe_metadata(action: str) -> None:
    class _Registry:
        def metadata_of(self, action_name: str) -> ActionMetadata | None:
            del action_name
            return ActionMetadata(read_only=True, destructive=False, risk="none")

    registry = _Registry()
    assert is_deletion_action(action)
    assert action_guard(action, registry) == "name"
    assert step_is_deletion(PlanStep(action=action), registry)


def test_dropdown_is_not_a_deletion_segment() -> None:
    assert not is_deletion_action("dropdown")
    assert not is_deletion_action("look")
    assert not params_request_deletion({"label": "dropdown"})


def test_unregistered_call_is_denied_without_listing_it() -> None:
    class _Registry:
        def metadata_of(self, action_name: str) -> ActionMetadata | None:
            if action_name == "look":
                return ActionMetadata(read_only=True, destructive=False, risk="none")
            return None

    registry = _Registry()
    assert action_guard("shutil.rmtree", registry) == "name"
    assert action_guard("Path.unlink", registry) == "name"
    assert action_guard("kadr.erase", registry) == "name"
    assert action_guard("look", registry) is None
    assert step_is_deletion(PlanStep(action="shutil.rmtree"), registry)


def test_metadata_does_not_waive_scene_or_token_deletion() -> None:
    class _Registry:
        def metadata_of(self, action_name: str) -> ActionMetadata | None:
            if action_name in {"scene.delete", "os.remove", "remove_watermark", "character.drop"}:
                return ActionMetadata(read_only=True, destructive=False, risk="none")
            return None

    registry = _Registry()
    assert action_guard("scene.delete", registry) == "name"
    assert action_guard("os.remove", registry) == "name"
    assert action_guard("remove_watermark", registry) == "name"
    assert action_guard("character.drop", registry) == "name"


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


@pytest.mark.skipif(os.name == "nt", reason="WinError 1314: creating a symlink needs privilege")
def test_symlink_outside_root_is_rejected(tmp_path) -> None:
    root = tmp_path / "allowed"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "scene.png"
    secret.write_text("x", encoding="utf-8")
    link = root / "alias"
    try:
        os.symlink(secret, link)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("WinError 1314")
        raise
    assert not path_is_within(root, link)
