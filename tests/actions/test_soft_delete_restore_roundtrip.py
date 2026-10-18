"""N4: frame.restore / group.restore / prompt.restore_master.

Мягкое удаление montage.delete_frame и montage.delete_child_frame пишет
соседняя ветка адаптера. Здесь снимок кладётся до уже существующего
жёсткого удаления, затем восстановление читает snapshot_id.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app.models import Frame, Scene
from app.orchestrator.actions.contracts import SOFT_DELETE_RESTORE, ActionResult
from app.services import node_groups as ng
from app.services.action_layer import executor
from app.services.action_layer.soft_restore import capture_frame, remember_soft_delete
from app.services.action_layer.spec import ensure_builtin_actions, registry
from app.services.frame_tombstones import deleted_numbers
from app.services.node_groups import _group_to_dict, get_node_group, save_custom_group
from tests.action_layer_support import env

BASE = "/api/action-layer"
_SHOTS = [{"id": "s1", "текст": "кусок закадра", "активен": True}]


@pytest.fixture(autouse=True)
def _builtin():
    ensure_builtin_actions()


@pytest.fixture
def prompts_root(tmp_path, monkeypatch):
    from app.services import prompt_library as pl

    root = tmp_path / "prompts_root"
    root.mkdir()
    monkeypatch.setattr(pl, "PROMPTS_ROOT", root)
    (pl.step_dir("plan") / "default.md").write_text("старый текст", encoding="utf-8")
    return root


async def _run(env, steps, *, approve=True):
    r = await env.client.post(f"{BASE}/plans/preview", json={"steps": steps})
    assert r.status_code == 200, r.text
    plan = r.json()["plan_id"]
    if approve:
        approved = await env.client.post(f"{BASE}/plans/{plan}/approve")
        assert approved.status_code == 200, approved.text
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    return (await env.client.get(f"{BASE}/plans/{plan}")).json()


def _snap_ctx() -> SimpleNamespace:
    return SimpleNamespace(plan_id="", step_index=None, operator="n4")


def _assert_restore(view: dict, action: str, snapshot_id: str) -> None:
    assert view["status"] == "succeeded", view
    parsed = ActionResult.model_validate(view["steps"][0]["result"])
    assert parsed.status == "ok" and parsed.error is None
    assert parsed.result is not None
    assert parsed.result.action == action
    assert parsed.result.snapshot_id == snapshot_id
    assert parsed.result.action == SOFT_DELETE_RESTORE[
        {
            "frame.restore": "montage.delete_frame",
            "group.restore": "group.delete",
            "prompt.restore_master": "prompt.delete_master",
        }[action]
    ]


async def test_soft_delete_restore_roundtrip(env, prompts_root, monkeypatch) -> None:
    for delete_id, restore_id in SOFT_DELETE_RESTORE.items():
        spec = registry.get(restore_id)
        assert spec.risk == "destructive" and spec.destructive and spec.needs_approval
        assert spec.id == restore_id
        assert delete_id  # пара из контракта зарегистрирована восстановлением

    monkeypatch.setattr(ng, "custom_groups_dir", lambda: env.tmp_path / "node_groups")
    pid = await env.add_project(frames=3)
    ids = await env.frame_ids(pid)
    async with env.factory() as session:
        scenes_before = await session.scalar(select(func.count()).select_from(Scene))
        frame = await session.get(Frame, ids[2])
        frame.attrs = {"кадры": [dict(shot) for shot in _SHOTS]}
        await session.commit()
        kept = {
            n: (await session.get(Frame, ids[n])).voiceover_text for n in (1, 3)
        }
    before = await capture_frame(pid, ids[2])
    assert before is not None and before["attrs"]["кадры"] == _SHOTS
    frame_snap = await remember_soft_delete(
        _snap_ctx(),
        action_id="montage.delete_frame",
        project_id=pid,
        kind="frame",
        target={"frame_id": ids[2], "number": 2},
        before=before,
    )

    deleted = await _run(env, [{
        "action_id": "montage.delete_frame",
        "params": {"project_id": pid, "frame_id": ids[2]},
    }])
    assert deleted["status"] == "succeeded", deleted
    async with env.factory() as session:
        from app.models import Project

        gone = await session.scalar(
            select(Frame).where(Frame.project_id == pid, Frame.number == 2)
        )
        assert gone is None
        assert 2 in deleted_numbers(await session.get(Project, pid))

    restored = await _run(env, [{
        "action_id": "frame.restore",
        "params": {"project_id": pid, "snapshot_id": frame_snap},
    }])
    _assert_restore(restored, "frame.restore", frame_snap)
    async with env.factory() as session:
        from app.models import Project

        row = await session.scalar(
            select(Frame).where(Frame.project_id == pid, Frame.number == 2)
        )
        assert row is not None
        assert row.voiceover_text == before["voiceover_text"]
        assert row.image_prompt == before["image_prompt"]
        assert row.animation_prompt == before["animation_prompt"]
        assert row.attrs["кадры"] == _SHOTS
        assert all(shot["активен"] is True for shot in row.attrs["кадры"])
        assert row.scene_id is None
        assert 2 not in deleted_numbers(await session.get(Project, pid))
        for number, text in kept.items():
            sibling = await session.scalar(
                select(Frame).where(Frame.project_id == pid, Frame.number == number)
            )
            assert sibling is not None and sibling.id == ids[number]
            assert sibling.voiceover_text == text
        scenes_after = await session.scalar(select(func.count()).select_from(Scene))
        assert scenes_after == scenes_before

    spec = {
        "group_id": "n4restore",
        "title": "Группа N4",
        "category": "planning",
        "nodes": [{"local_key": "hero", "label": "Герой", "node_type": "excel_gpt"}],
    }
    save_custom_group(spec)
    group_before = _group_to_dict(get_node_group("n4restore"))
    group_snap = await remember_soft_delete(
        _snap_ctx(),
        action_id="group.delete",
        project_id=None,
        kind="group",
        target={"group_id": "n4restore"},
        before=group_before,
    )
    gone_group = await _run(env, [{
        "action_id": "group.delete",
        "params": {"group_id": "n4restore"},
    }])
    assert gone_group["status"] == "succeeded", gone_group
    assert get_node_group("n4restore") is None
    back_group = await _run(env, [{
        "action_id": "group.restore",
        "params": {"snapshot_id": group_snap},
    }])
    _assert_restore(back_group, "group.restore", group_snap)
    group = get_node_group("n4restore")
    assert group is not None
    assert group.title == "Группа N4"
    assert [node.local_key for node in group.nodes] == ["hero"]

    created = await _run(env, [{
        "action_id": "prompt.create_master",
        "params": {"step_code": "plan", "name": "fresh", "content": "текст до удаления"},
    }])
    assert created["status"] == "succeeded", created
    prompt_path = prompts_root / "01_plan" / "fresh.md"
    assert prompt_path.read_text(encoding="utf-8") == "текст до удаления"
    prompt_before = {"step_code": "plan", "name": "fresh", "content": "текст до удаления"}
    prompt_snap = await remember_soft_delete(
        _snap_ctx(),
        action_id="prompt.delete_master",
        project_id=None,
        kind="master_prompt",
        target={"step_code": "plan", "name": "fresh"},
        before=prompt_before,
    )
    gone_prompt = await _run(env, [{
        "action_id": "prompt.delete_master",
        "params": {"step_code": "plan", "name": "fresh"},
    }])
    assert gone_prompt["status"] == "succeeded", gone_prompt
    assert not prompt_path.exists()
    back_prompt = await _run(env, [{
        "action_id": "prompt.restore_master",
        "params": {"snapshot_id": prompt_snap},
    }])
    _assert_restore(back_prompt, "prompt.restore_master", prompt_snap)
    assert prompt_path.read_text(encoding="utf-8") == "текст до удаления"
    assert (prompts_root / "01_plan" / "default.md").read_text(encoding="utf-8") == "старый текст"


async def test_production_delete_snapshot_restores_without_test_helper(
    env, prompts_root, monkeypatch
) -> None:
    """snapshot_id приходит из исполнителя удаления, тест хелперы не зовёт."""
    monkeypatch.setattr(ng, "custom_groups_dir", lambda: env.tmp_path / "node_groups")
    pid = await env.add_project(frames=3)
    ids = await env.frame_ids(pid)
    async with env.factory() as session:
        frame = await session.get(Frame, ids[2])
        voice = frame.voiceover_text
        frame.attrs = {"кадры": [dict(shot) for shot in _SHOTS]}
        await session.commit()

    deleted = await _run(env, [{
        "action_id": "montage.delete_frame",
        "params": {"project_id": pid, "frame_id": ids[2]},
    }])
    assert deleted["status"] == "succeeded", deleted
    result = deleted["steps"][0]["result"]
    assert result["deleted"] == 1
    snap = result["snapshot_id"]
    assert isinstance(snap, str) and snap.strip()
    restored = await _run(env, [{
        "action_id": "frame.restore",
        "params": {"project_id": pid, "snapshot_id": snap},
    }])
    _assert_restore(restored, "frame.restore", snap)
    async with env.factory() as session:
        row = await session.scalar(
            select(Frame).where(Frame.project_id == pid, Frame.number == 2)
        )
        assert row is not None
        assert row.voiceover_text == voice
        assert row.attrs["кадры"] == _SHOTS

    spec = {
        "group_id": "n4live",
        "title": "Группа до удаления",
        "category": "planning",
        "nodes": [{"local_key": "hero", "label": "Герой", "node_type": "excel_gpt"}],
    }
    save_custom_group(spec)
    gone_group = await _run(env, [{
        "action_id": "group.delete",
        "params": {"group_id": "n4live"},
    }])
    assert gone_group["status"] == "succeeded", gone_group
    group_snap = gone_group["steps"][0]["result"]["snapshot_id"]
    assert isinstance(group_snap, str) and group_snap.strip()
    assert get_node_group("n4live") is None
    back_group = await _run(env, [{
        "action_id": "group.restore",
        "params": {"snapshot_id": group_snap},
    }])
    _assert_restore(back_group, "group.restore", group_snap)
    group = get_node_group("n4live")
    assert group is not None and group.title == "Группа до удаления"

    created = await _run(env, [{
        "action_id": "prompt.create_master",
        "params": {"step_code": "plan", "name": "livedel", "content": "текст до удаления"},
    }])
    assert created["status"] == "succeeded", created
    prompt_path = prompts_root / "01_plan" / "livedel.md"
    gone_prompt = await _run(env, [{
        "action_id": "prompt.delete_master",
        "params": {"step_code": "plan", "name": "livedel"},
    }])
    assert gone_prompt["status"] == "succeeded", gone_prompt
    prompt_snap = gone_prompt["steps"][0]["result"]["snapshot_id"]
    assert isinstance(prompt_snap, str) and prompt_snap.strip()
    assert not prompt_path.exists()
    back_prompt = await _run(env, [{
        "action_id": "prompt.restore_master",
        "params": {"snapshot_id": prompt_snap},
    }])
    _assert_restore(back_prompt, "prompt.restore_master", prompt_snap)
    assert prompt_path.read_text(encoding="utf-8") == "текст до удаления"


async def test_frame_restore_occupied_number_does_not_overwrite(env) -> None:
    pid = await env.add_project(frames=2)
    ids = await env.frame_ids(pid)
    live_shots = [{"id": "s1", "текст": "живой", "активен": True}]
    async with env.factory() as session:
        frame = await session.get(Frame, ids[1])
        frame.voiceover_text = "живой текст"
        frame.attrs = {"кадры": [dict(shot) for shot in live_shots]}
        await session.commit()
    before = await capture_frame(pid, ids[1])
    assert before is not None
    before["voiceover_text"] = "чужой текст"
    before["attrs"] = {"кадры": [{"id": "s9", "текст": "чужой", "активен": False}]}
    snap = await remember_soft_delete(
        _snap_ctx(),
        action_id="montage.delete_frame",
        project_id=pid,
        kind="frame",
        target={"frame_id": ids[1], "number": 1},
        before=before,
    )
    restored = await _run(env, [{
        "action_id": "frame.restore",
        "params": {"project_id": pid, "snapshot_id": snap},
    }])
    assert restored["status"] == "failed", restored
    step = restored["steps"][0]
    assert step["status"] == "failed"
    assert "conflict" in step["error"]
    async with env.factory() as session:
        row = await session.get(Frame, ids[1])
        assert row.voiceover_text == "живой текст"
        assert row.attrs["кадры"] == live_shots
