"""F3: свой ключ meta, версия, снимок и откат через history.rollback."""

from __future__ import annotations

import asyncio
import copy

import pytest

from app.models import Project
from app.orchestrator.node_store.meta_book import (
    MetaBook, VersionConflict, expected_version, load_book,
)
from app.services.action_layer import executor, store
from app.services.action_layer.builtin_group import _node_set_disabled, _node_set_prompt_variant
from app.services.action_layer.builtin_montage_more import _set_assemble_params
from app.services.action_layer.context import ActionCallError
from app.services.action_layer.spec import ensure_builtin_actions
from app.services.project_meta import merge_project_meta
from tests.action_layer_support import env

BASE = "/api/action-layer"


def test_omitted_expected_meta_version_is_a_conflict() -> None:
    book = load_book({
        "meta_version": 3,
        "prompt_slot_variants": {"hero": {"main": "old.md"}},
    })
    with pytest.raises(VersionConflict):
        expected_version({}, book)
    with pytest.raises(VersionConflict):
        book.set_prompt_variant(
            node_key="hero",
            slot_id="main",
            variant="new.md",
            version=expected_version({"expected_meta_version": 1}, book),
        )
    assert book.version == 3
    assert book.meta["prompt_slot_variants"]["hero"]["main"] == "old.md"
    book.set_prompt_variant(
        node_key="hero",
        slot_id="alt",
        variant="b.md",
        version=expected_version({"expected_meta_version": 3}, book),
    )
    assert book.meta["prompt_slot_variants"]["hero"] == {"main": "old.md", "alt": "b.md"}
    with pytest.raises(VersionConflict):
        expected_version({"expected_meta_version": "нет"}, book)


def test_set_prompt_variant_concurrent_slots_both_kept() -> None:
    book = MetaBook()
    seen = book.version
    book.set_prompt_variant(
        node_key="hero", slot_id="main", variant="a.md", version=seen
    )
    with pytest.raises(VersionConflict) as exc:
        book.set_prompt_variant(
            node_key="hero", slot_id="alt", variant="b.md", version=seen
        )
    assert exc.value.status_code == 409
    book.set_prompt_variant(
        node_key="hero", slot_id="alt", variant="b.md", version=book.version
    )
    assert book.meta["prompt_slot_variants"]["hero"] == {
        "main": "a.md",
        "alt": "b.md",
    }

    state = {"meta": {"prompt_slot_variants": {"hero": {"main": "old.md"}}}}

    async def _both() -> None:
        # Оба GET обязаны увидеть одно и то же meta до любого PATCH.
        lock = asyncio.Lock()
        gate = asyncio.Event()
        gets = {"n": 0}

        class Ctx:
            async def call(self, method, path, *, params=None, json=None, timeout=0):
                if method == "GET":
                    async with lock:
                        snap = copy.deepcopy(state["meta"])
                        gets["n"] += 1
                        n = gets["n"]
                    if n < 2:
                        await gate.wait()
                    else:
                        gate.set()
                    return {"id": 1, "status": "plan_ready", "meta": snap}
                if method == "PATCH":
                    async with lock:
                        state["meta"] = merge_project_meta(
                            state["meta"], (json or {}).get("meta") or {}, source="test"
                        )
                    return {}
                raise AssertionError(method)

        ctx = Ctx()
        await asyncio.gather(
            _node_set_prompt_variant(
                ctx, {"project_id": 1, "node_key": "hero", "slot_id": "main", "variant": "a.md"}
            ),
            _node_set_prompt_variant(
                ctx, {"project_id": 1, "node_key": "hero", "slot_id": "alt", "variant": "b.md"}
            ),
        )

    asyncio.run(_both())
    assert state["meta"]["prompt_slot_variants"]["hero"] == {"main": "a.md", "alt": "b.md"}
    assert "old.md" not in state["meta"]["prompt_slot_variants"]["hero"].values()


def test_set_assemble_params_does_not_clobber_other_step_params() -> None:
    book = MetaBook(
        meta={
            "node_step_params": {
                "script": {"tone": "dry"},
                "assemble": {"bgm_level": 0.1, "subtitles_enabled": True},
            }
        }
    )
    book.set_assemble_params(values={"bgm_level": 0.4}, version=book.version)
    params = book.meta["node_step_params"]
    assert params["script"] == {"tone": "dry"}
    assert params["assemble"]["bgm_level"] == 0.4
    assert params["assemble"]["subtitles_enabled"] is True

    state = {
        "meta": {
            "node_step_params": {
                "split": {"cell_max_chars": 9},
                "assemble": {"bgm_level": 10, "subtitles_enabled": True},
            }
        }
    }

    async def _run() -> None:
        class Ctx:
            async def call(self, method, path, *, params=None, json=None, timeout=0):
                if method == "GET":
                    return {"id": 1, "status": "plan_ready", "meta": copy.deepcopy(state["meta"])}
                if method == "PATCH":
                    state["meta"]["node_step_params"]["split"]["cell_max_chars"] = 99
                    state["meta"] = merge_project_meta(
                        state["meta"], (json or {}).get("meta") or {}, source="test"
                    )
                    return {}
                raise AssertionError(method)

        await _set_assemble_params(Ctx(), {"project_id": 1, "bgm_level": 40})

    asyncio.run(_run())
    saved = state["meta"]["node_step_params"]
    assert saved["split"]["cell_max_chars"] == 99
    assert saved["assemble"]["bgm_level"] == 40
    assert saved["assemble"]["subtitles_enabled"] is True


def test_node_param_actions_write_snapshot() -> None:
    book = MetaBook()
    book.set_config(node_key="hero", fields={"role": "writer"}, version=book.version)
    book.set_prompt_variant(
        node_key="hero", slot_id="main", variant="a.md", version=book.version
    )
    book.set_assemble_params(values={"bgm_level": 0.2}, version=book.version)
    seen = book.version
    book.set_disabled(node_key="hero", disabled=True, version=seen)
    with pytest.raises(VersionConflict) as exc:
        book.set_disabled(node_key="plan", disabled=True, version=seen)
    assert exc.value.status_code == 409
    book.set_disabled(node_key="plan", disabled=True, version=book.version)

    actions = [row["action"] for row in book.list_snapshots()]
    assert actions == [
        "node.set_config",
        "node.set_prompt_variant",
        "montage.set_assemble_params",
        "node.set_disabled",
        "node.set_disabled",
    ]
    for row in book.list_snapshots():
        assert "before" in row and "after" in row
    assert book.meta["disabled_nodes"] == ["hero", "plan"]


def test_patch_meta_conflict_rejected() -> None:
    book = MetaBook()
    book.patch_meta("alpha", 1, version=0)
    with pytest.raises(VersionConflict) as exc:
        book.patch_meta("beta", 2, version=0)
    assert exc.value.status_code == 409
    assert book.meta["alpha"] == 1
    assert "beta" not in book.meta
    book.patch_meta("beta", 2, version=book.version)
    assert book.meta["alpha"] == 1
    assert book.meta["beta"] == 2

    async def _stale() -> None:
        class Ctx:
            async def call(self, method, path, *, params=None, json=None, timeout=0):
                if method == "GET":
                    return {"id": 1, "status": "plan_ready", "meta": {"meta_version": 3}}
                raise AssertionError("устаревшая версия не должна писать meta")

        with pytest.raises(ActionCallError) as err:
            await _node_set_prompt_variant(
                Ctx(),
                {
                    "project_id": 1,
                    "node_key": "hero",
                    "variant": "a.md",
                    "expected_meta_version": 0,
                },
            )
        assert err.value.status == 409
        with pytest.raises(ActionCallError) as err2:
            await _node_set_disabled(
                Ctx(),
                {
                    "project_id": 1,
                    "node_key": "hero",
                    "disabled": True,
                    "expected_meta_version": 1,
                },
            )
        assert err2.value.status == 409

    asyncio.run(_stale())


def test_node_set_config_keeps_previous_value() -> None:
    book = MetaBook()
    book.set_config(
        node_key="hero",
        fields={"role": "writer", "outputMode": "db"},
        version=book.version,
    )
    book.set_config(node_key="hero", fields={"role": "editor"}, version=book.version)
    assert book.meta["node_config"]["hero"]["outputMode"] == "db"
    snaps = [row for row in book.list_snapshots() if row["action"] == "node.set_config"]
    assert snaps[-1]["before"]["role"] == "writer"
    assert snaps[-1]["after"]["role"] == "editor"
    book.rollback(snaps[-1]["id"])
    assert book.meta["node_config"]["hero"] == {"role": "writer", "outputMode": "db"}


@pytest.fixture(autouse=True)
def _builtin():
    ensure_builtin_actions()


def step(action_id, /, **params):
    return {"action_id": action_id, "params": params}


async def _run(env, steps, *, approve=False):
    r = await env.client.post(f"{BASE}/plans/preview", json={"steps": steps})
    assert r.status_code == 200, r.text
    plan = r.json()["plan_id"]
    if approve:
        assert (await env.client.post(f"{BASE}/plans/{plan}/approve")).status_code == 200
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    return (await env.client.get(f"{BASE}/plans/{plan}")).json()


async def _result(env, steps, *, approve=False) -> dict:
    view = await _run(env, steps, approve=approve)
    assert view["status"] == "succeeded", view
    return view["steps"][-1]["result"]


async def _meta(env, pid) -> dict:
    return (await env.client.get(f"/api/projects/{pid}")).json()["meta"]


async def test_param_actions_rollback_via_history(env) -> None:
    pid = await env.add_project(frames=1)
    async with env.factory() as s:
        project = await s.get(Project, pid)
        project.meta = {
            "canvas_graph": {
                "nodes": [{"id": "hero", "type": "excel_gpt", "data": {}}],
                "edges": [],
            },
            "prompt_slot_variants": {"other": {"main": "keep.md"}},
            "disabled_nodes": ["n_x"],
            "node_step_params": {
                "split": {"cell_max_chars": 12},
                "assemble": {"bgm_level": 10, "subtitles_enabled": False},
            },
        }
        await s.commit()

    asm = await _result(
        env, [step("montage.set_assemble_params", project_id=pid, bgm_level=40)]
    )
    assert asm["rollback_possible"] is True
    snap = await store.get_snapshot(asm["snapshot_id"])
    assert snap.before["bgm_level"] == 10 and snap.action_id == "montage.set_assemble_params"
    meta = await _meta(env, pid)
    assert meta["node_step_params"]["split"]["cell_max_chars"] == 12
    assert meta["node_step_params"]["assemble"]["bgm_level"] == 40
    assert meta["node_step_params"]["assemble"]["subtitles_enabled"] is False

    await _result(
        env,
        [step("node.set_prompt_variant", project_id=pid, node_key="hero",
              slot_id="alt", variant="b.md")],
    )
    meta = await _meta(env, pid)
    assert meta["prompt_slot_variants"]["other"] == {"main": "keep.md"}
    assert meta["prompt_slot_variants"]["hero"]["alt"] == "b.md"

    first_cfg = await _result(
        env, [step("node.set_config", project_id=pid, node_key="hero", role="review")]
    )
    cfg_snap = await store.get_snapshot(first_cfg["snapshot_id"])
    assert cfg_snap.before["role"] != "review"
    second_cfg = await _result(
        env, [step("node.set_config", project_id=pid, node_key="hero", role="assist")]
    )
    resolve = (await env.client.get(f"/api/projects/{pid}/gpt-operator/hero/resolve")).json()
    assert resolve["config"]["role"] == "assist"
    listed = await _result(env, [step("history.list_snapshots", project_id=pid, limit=20)])
    actions = {row["action_id"] for row in listed["snapshots"]}
    assert {"montage.set_assemble_params", "node.set_prompt_variant", "node.set_config"} <= actions

    disabled = await _result(
        env, [step("node.set_disabled", project_id=pid, node_key="hero", disabled=True)]
    )
    dis_snap = await store.get_snapshot(disabled["snapshot_id"])
    assert dis_snap.before is False and dis_snap.after is True
    meta = await _meta(env, pid)
    assert meta["disabled_nodes"] == ["n_x", "hero"] or set(meta["disabled_nodes"]) == {"n_x", "hero"}

    back = await _run(env, [step("history.rollback", snapshot_id=asm["snapshot_id"])], approve=True)
    assert back["status"] == "succeeded", back
    meta = await _meta(env, pid)
    assert meta["node_step_params"]["assemble"]["bgm_level"] == 10
    assert meta["node_step_params"]["split"]["cell_max_chars"] == 12
    assert meta["prompt_slot_variants"]["hero"]["alt"] == "b.md"

    back_cfg = await _run(
        env, [step("history.rollback", snapshot_id=second_cfg["snapshot_id"])], approve=True
    )
    assert back_cfg["status"] == "succeeded", back_cfg
    resolve = (await env.client.get(f"/api/projects/{pid}/gpt-operator/hero/resolve")).json()
    assert resolve["config"]["role"] == "review"

    back_dis = await _run(
        env, [step("history.rollback", snapshot_id=disabled["snapshot_id"])], approve=True
    )
    assert back_dis["status"] == "succeeded", back_dis
    meta = await _meta(env, pid)
    assert "hero" not in [str(x) for x in meta.get("disabled_nodes") or []]
    assert "n_x" in [str(x) for x in meta.get("disabled_nodes") or []]
