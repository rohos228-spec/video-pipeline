"""F5: откат мастер-промта отказывается 409, если текст уже меняли после снимка."""

from __future__ import annotations

import pytest

from app.services.action_layer import executor
from app.services.action_layer.spec import ensure_builtin_actions
from tests.action_layer_support import env

BASE = "/api/action-layer"


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
    (pl.step_dir("plan") / "variant_a.md").write_text("вариант а", encoding="utf-8")
    return root


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


async def test_rollback_master_prompt_refuses_if_changed_since(env, prompts_root) -> None:
    saved = await _run(
        env,
        [step("prompt.save_master", step_code="plan", name="variant_a", content="новый текст")],
        approve=True,
    )
    assert saved["status"] == "succeeded", saved
    snap_id = saved["steps"][0]["result"]["snapshot_id"]
    path = prompts_root / "01_plan" / "variant_a.md"
    path.write_text("чужая правка", encoding="utf-8")

    back = await _run(env, [step("history.rollback", snapshot_id=snap_id)], approve=True)
    assert back["status"] == "failed", back
    err = str(back["steps"][0]["error"])
    assert "409" in err
    assert path.read_text(encoding="utf-8") == "чужая правка"
