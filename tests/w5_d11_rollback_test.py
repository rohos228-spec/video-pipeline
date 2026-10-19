from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Frame
from app.services.action_layer import builtin_wave5_history as hist
from app.services.action_layer import executor, store
from app.services.action_layer.spec import ensure_builtin_actions, registry
from tests.action_layer_support import env

BASE = "/api/action-layer"


@pytest.fixture(autouse=True)
def _builtin():
    ensure_builtin_actions()


@pytest.fixture
def not_managed(monkeypatch):
    async def _no(pid):
        return False
    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", _no)


@pytest.fixture
def prompts_root(tmp_path, monkeypatch):
    from app.services import prompt_library as pl
    root = tmp_path / "prompts_root"
    root.mkdir()
    monkeypatch.setattr(pl, "PROMPTS_ROOT", root)
    (pl.step_dir("plan") / "default.md").write_text("старый текст", encoding="utf-8")
    (pl.step_dir("plan") / "variant_a.md").write_text("вариант а", encoding="utf-8")
    return root


async def _run(env, steps, *, approve=False):
    r = await env.client.post(f"{BASE}/plans/preview", json={"steps": steps})
    assert r.status_code == 200, r.text
    plan = r.json()["plan_id"]
    if approve:
        assert (await env.client.post(f"{BASE}/plans/{plan}/approve")).status_code == 200
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    return (await env.client.get(f"{BASE}/plans/{plan}")).json()


def step(action_id, /, **params):
    return {"action_id": action_id, "params": params}


async def _result(env, steps, *, approve=False) -> dict:
    view = await _run(env, steps, approve=approve)
    assert view["status"] == "succeeded", view
    return view["steps"][-1]["result"]


async def _rollback(env, snapshot_id, **extra):
    return await _run(env, [step("history.rollback", snapshot_id=snapshot_id, **extra)],
                      approve=True)


def test_wrapped_actions_are_listed_and_keep_their_tier() -> None:
    from app.services.action_layer.builtin_wave5 import WAVE5_CHANGED_IDS

    wrapped = set(hist.HANDLERS)
    core = {"montage.set_voiceover", "montage.set_coverage", "montage.set_anchors",
            "montage.set_scene_chain", "shot.edit_cell_voiceover", "shot.edit_field",
            "montage.upload_voice", "montage.upload_music", "prompt.save_master"}
    param = {"node.set_config", "node.set_prompt_variant", "node.set_disabled",
             "montage.set_assemble_params"}
    assert wrapped == core | param
    assert core <= set(WAVE5_CHANGED_IDS)
    for aid in wrapped:
        spec = registry.get(aid)
        assert "history.rollback" in spec.description_ru, aid
        assert "Прежний текст не сохраняется" not in spec.description_ru, aid
    assert registry.get("montage.set_voiceover").risk == "local_safe"
    assert registry.get("montage.set_anchors").risk == "destructive"



async def test_voiceover_edit_is_snapshotted_and_rolled_back(env, not_managed) -> None:
    pid = await env.add_project(frames=2)
    fid = (await env.frame_ids(pid))[1]
    out = await _result(env, [step("montage.set_voiceover", project_id=pid, frame_id=fid,
                                   text="новая реплика")])
    assert out["voiceover_text"] == "новая реплика" and out["rollback_possible"] is True
    snap = await store.get_snapshot(out["snapshot_id"])
    assert (snap.before, snap.after, snap.kind) == ("реплика 1", "новая реплика", "voiceover")
    assert snap.restorable and snap.project_id == pid

    listed = await _result(env, [step("history.list_snapshots", project_id=pid)])
    assert listed["snapshots"][0]["snapshot_id"] == snap.id
    assert listed["snapshots"][0]["before"] == "реплика 1"

    back = await _rollback(env, snap.id)
    assert back["status"] == "succeeded", back
    async with env.factory() as s:
        assert (await s.get(Frame, fid)).voiceover_text == "реплика 1"
    assert (await store.get_snapshot(snap.id)).restored_at is not None
    again = await env.client.post(f"{BASE}/plans/preview",
                                  json={"steps": [step("history.rollback", snapshot_id=snap.id)]})
    assert again.status_code == 422 and "snapshot_restorable" in str(again.json())


async def test_rollback_is_itself_reversible_and_journalled(env, not_managed) -> None:
    pid = await env.add_project(frames=1)
    fid = (await env.frame_ids(pid))[1]
    out = await _result(env, [step("montage.set_voiceover", project_id=pid, frame_id=fid,
                                   text="правка")])
    back = await _rollback(env, out["snapshot_id"])
    reverse_id = back["steps"][0]["result"]["reverse_snapshot_id"]
    reverse = await store.get_snapshot(reverse_id)
    assert (reverse.before, reverse.after) == ("правка", "реплика 1") and reverse.restorable
    await _rollback(env, reverse_id)
    async with env.factory() as s:
        assert (await s.get(Frame, fid)).voiceover_text == "правка"
        events = [r.event for r in (await s.execute(select(store.ActionHistory))).scalars()]
    assert events.count("snapshot_saved") == 3 and events.count("snapshot_restored") == 2


async def test_rollback_refuses_to_overwrite_a_later_edit_unless_forced(env, not_managed) -> None:
    pid = await env.add_project(frames=1)
    fid = (await env.frame_ids(pid))[1]
    first = await _result(env, [step("montage.set_voiceover", project_id=pid, frame_id=fid,
                                     text="первая")])
    await _result(env, [step("montage.set_voiceover", project_id=pid, frame_id=fid,
                             text="вторая")])
    refused = await _rollback(env, first["snapshot_id"])
    assert refused["status"] == "failed" and "изменилось после правки" in str(
        refused["steps"][0]["error"])
    async with env.factory() as s:
        assert (await s.get(Frame, fid)).voiceover_text == "вторая"
    forced = await _rollback(env, first["snapshot_id"], force=True)
    assert forced["status"] == "succeeded"
    async with env.factory() as s:
        assert (await s.get(Frame, fid)).voiceover_text == "реплика 1"


async def test_secret_looking_text_is_recorded_but_not_restorable(env, not_managed) -> None:
    pid = await env.add_project(frames=1)
    fid = (await env.frame_ids(pid))[1]
    async with env.factory() as s:
        (await s.get(Frame, fid)).voiceover_text = "ключ token: abcdef123456 остался"
        await s.commit()
    out = await _result(env, [step("montage.set_voiceover", project_id=pid, frame_id=fid,
                                   text="чисто")])
    snap = await store.get_snapshot(out["snapshot_id"])
    assert snap.restorable is False and "[redacted]" in snap.before
    assert "abcdef123456" not in snap.before and "секрет" in snap.note
    refused = await env.client.post(f"{BASE}/plans/preview",
                                    json={"steps": [step("history.rollback",
                                                         snapshot_id=snap.id)]})
    assert refused.status_code == 422



async def test_coverage_plan_is_snapshotted_and_restored(env, not_managed) -> None:
    pid = await env.add_project(frames=1)
    first = await _result(env, [step("montage.set_coverage", project_id=pid, frame_number=1,
                                     type="coverage_plan", value="КРУПНЫЙ")])
    second = await _result(env, [step("montage.set_coverage", project_id=pid, frame_number=1,
                                      type="coverage_plan", value="ДАЛЬНИЙ")])
    snap = await store.get_snapshot(second["snapshot_id"])
    assert snap.before == "КРУПНЫЙ" and snap.after == "ДАЛЬНИЙ" and snap.restorable
    back = await _rollback(env, snap.id)
    assert back["status"] == "succeeded", back
    board = (await env.client.get(f"/api/projects/{pid}/montage-board")).json()
    assert board["frames"][0]["shot_plan"] == "КРУПНЫЙ"
    empty = await store.get_snapshot(first["snapshot_id"])
    assert empty.restorable is False and "пуст" in empty.note



async def test_structural_edit_records_previous_state_without_promising_rollback(
        env, not_managed) -> None:
    pid = await env.add_project(frames=1)
    out = await _result(env, [step("montage.set_scene_chain", project_id=pid, frame_number=1,
                                   action="входит → садится")], approve=True)
    assert out["rollback_possible"] is False
    snap = await store.get_snapshot(out["snapshot_id"])
    assert snap.kind == "scene_structure" and isinstance(snap.before, dict)
    assert "структурная правка" in snap.note
    refused = await env.client.post(f"{BASE}/plans/preview",
                                    json={"steps": [step("history.rollback",
                                                         snapshot_id=snap.id)]})
    assert refused.status_code == 422 and "snapshot_restorable" in str(refused.json())



async def _with_uuid(env, pid):
    async with env.factory() as s:
        fr = (await s.execute(select(Frame).where(Frame.project_id == pid))).scalars().first()
        fr.uuid = "cell1234abcd"
        await s.commit()
    return "cell1234abcd"


async def test_shot_cell_voiceover_and_field_rollback(env, not_managed) -> None:
    pid = await env.add_project(frames=1)
    uid = await _with_uuid(env, pid)
    cell = await _result(env, [step("shot.edit_cell_voiceover", project_id=pid,
                                    parent_uuid=uid, voiceover="другой закадр")])
    snap = await store.get_snapshot(cell["snapshot_id"])
    assert snap.before == "реплика 1" and snap.kind == "cell_voiceover" and snap.restorable
    assert (await _rollback(env, snap.id))["status"] == "succeeded"
    async with env.factory() as s:
        fr = (await s.execute(select(Frame).where(Frame.project_id == pid))).scalars().first()
        assert fr.voiceover_text == "реплика 1"

    field = await _result(env, [step("shot.edit_field", project_id=pid, frame_uuid=uid,
                                     field="img_prompt", value="новый промт")])
    fsnap = await store.get_snapshot(field["snapshot_id"])
    assert fsnap.before == "img 1" and fsnap.after == "новый промт" and fsnap.restorable
    assert (await _rollback(env, fsnap.id))["status"] == "succeeded"
    async with env.factory() as s:
        fr = (await s.execute(select(Frame).where(Frame.project_id == pid))).scalars().first()
        assert fr.image_prompt == "img 1"

    act = await _result(env, [step("shot.edit_field", project_id=pid, frame_uuid=uid,
                                   field="action", value="идёт")])
    asnap = await store.get_snapshot(act["snapshot_id"])
    assert asnap.before is None and asnap.after == "идёт" and asnap.restorable


async def test_shot_field_set_is_recorded_but_not_restorable(env, not_managed) -> None:
    pid = await env.add_project(frames=1)
    uid = await _with_uuid(env, pid)
    out = await _result(env, [step("shot.edit_field", project_id=pid, frame_uuid=uid,
                                   field="set", value="SET_03 кухня")])
    snap = await store.get_snapshot(out["snapshot_id"])
    assert snap.restorable is False and isinstance(snap.before, dict)



async def test_master_prompt_is_snapshotted_and_restored(env, prompts_root) -> None:
    out = await _result(env, [step("prompt.save_master", step_code="plan", name="variant_a",
                                   content="новый текст")], approve=True)
    assert out["previous_sha256"] == hashlib.sha256("вариант а".encode()).hexdigest()
    snap = await store.get_snapshot(out["snapshot_id"])
    assert (snap.before, snap.after, snap.project_id) == ("вариант а", "новый текст", None)
    back = await _rollback(env, snap.id)
    assert back["status"] == "succeeded", back
    assert (prompts_root / "01_plan" / "variant_a.md").read_text(encoding="utf-8") == "вариант а"
    pv = await env.client.post(f"{BASE}/plans/preview", json={"steps": [step(
        "prompt.save_master", step_code="plan", name="variant_a", content="ещё")]})
    effects = pv.json()["preview"]["destructive_effects"][0]["text"]
    assert "ГЛОБАЛЬНО" in effects and "history.rollback" in effects



def _wav(tag: bytes) -> bytes:
    return b"RIFF" + (36 + len(tag)).to_bytes(4, "little") + b"WAVEfmt " + tag * 4


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


async def test_voice_upload_keeps_a_copy_and_restores_it(env, not_managed) -> None:
    pid = await env.add_project(frames=1)
    one, two = _wav(b"1111"), _wav(b"2222")
    first = await _result(env, [step("montage.upload_voice", project_id=pid,
                                     filename="voice.wav", data_base64=_b64(one))], approve=True)
    assert first["rollback_possible"] is False
    s1 = await store.get_snapshot(first["snapshot_id"])
    assert s1.before == {"exists": False} and "откат невозможен" in s1.note

    second = await _result(env, [step("montage.upload_voice", project_id=pid,
                                      filename="voice.wav", data_base64=_b64(two))], approve=True)
    assert second["rollback_possible"] is True
    s2 = await store.get_snapshot(second["snapshot_id"])
    assert s2.before["sha256"] == hashlib.sha256(one).hexdigest()
    assert s2.after["sha256"] == hashlib.sha256(two).hexdigest()
    backup = Path(env.tmp_path) / s2.before["backup"]
    assert backup.read_bytes() == one and "action_layer_snapshots" in s2.before["backup"]
    assert "real_path" not in s2.before and "real_path" not in s2.after

    back = await _rollback(env, s2.id)
    assert back["status"] == "succeeded", back
    voice = next(p for p in Path(env.tmp_path).rglob("voice_full.wav")
                 if hist.SNAPSHOT_DIR not in p.parts)
    assert voice.read_bytes() == one

    s3 = await store.get_snapshot(back["steps"][0]["result"]["reverse_snapshot_id"])
    assert s3.before["sha256"] == hashlib.sha256(two).hexdigest()
    (Path(env.tmp_path) / s3.before["backup"]).write_bytes(_wav(b"evil"))
    broken = await _rollback(env, s3.id)
    assert broken["status"] == "failed"
    assert voice.read_bytes() == one


async def test_stub_context_bypasses_snapshots_and_never_touches_the_database() -> None:
    calls = []

    class Stub:
        async def call(self, method, path, **kw):
            calls.append((method, path))
            return {"id": 5, "number": 1, "voiceover_text": "т"}

    out = await registry.get("montage.set_voiceover").executor(
        Stub(), {"project_id": 1, "frame_id": 5, "text": "т"})
    assert "snapshot_id" not in out
    assert calls == [("PATCH", "/projects/1/montage-board/frames/5/voiceover")]
