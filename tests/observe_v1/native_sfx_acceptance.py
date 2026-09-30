"""Native SFX helper for the existing group_acceptance prepare/run seam.

Parent integration: call prepare before importing app.main, retain the existing
loopback FixtureHandler/send hook, then await run with the existing app/client.
There is no standalone runner or executor here. auto_mode remains false: two
ordinary step API starts exercise native plan -> generation without claiming
automatic continuation, managed admission, recovery or factory completion.

Mode: native-sfx. Cached rejection stays in the existing six persisted native
checkpoint tests. Ordinary sfx_plan API start wipes that cache even with
force_wipe=false, so this helper cannot prove worker checkpoint rejection.
The parent runner owns application cancellation and the diagnostic image/tools.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import sqlite3
import struct
import sys
import time
import wave
from pathlib import Path


PLAN = "native-sfx-plan-A"
GEN = "native-sfx-gen-B"
OUTSIDE = "native-sfx-outside-C"
MUSIC = "native-sfx-music-D"
PUBLISH = "native-sfx-publish-E"
SCOPE = (PLAN, GEN)
BOUNDARY = (OUTSIDE, MUSIC, PUBLISH)


def _rows(db, sql, parameters=()):
    cursor = db.execute(sql, parameters)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _snapshot(path):
    with sqlite3.connect(path) as db:
        projects = _rows(db, "SELECT id,status,auto_mode,meta FROM projects WHERE id=2")
        assert len(projects) == 1, "synthetic project 2 required"
        project = projects[0]
        project["meta"] = json.loads(project["meta"] or "{}")
        runs = _rows(db, "SELECT id,nodes_snapshot,edges_snapshot FROM workflow_runs WHERE project_id=2")
        assert len(runs) == 1, "one original synthetic workflow run required"
        run = runs[0]
        run["nodes_snapshot"] = json.loads(run["nodes_snapshot"])
        run["edges_snapshot"] = json.loads(run["edges_snapshot"])
        frames = _rows(db, "SELECT id,project_id,uuid,number,sort_key,voiceover_text,"
                       "duration_seconds,attrs FROM frames WHERE project_id=2 ORDER BY number")
        for frame in frames:
            frame["attrs"] = json.loads(frame["attrs"] or "{}")
        artifacts = _rows(db, "SELECT id,project_id,frame_id,kind,uuid,path FROM artifacts "
                          "WHERE project_id=2 ORDER BY id")
        nodes = _rows(db, "SELECT node_key,node_type,status,attempts FROM node_runs "
                      "WHERE workflow_run_id=? ORDER BY node_key", (run["id"],))
    return {"project": project, "run": run, "frames": frames, "artifacts": artifacts,
            "nodes": {node["node_key"]: node for node in nodes}}


def prepare(fixtures, root, mode):
    """Initialize only the copied synthetic input before the original app starts."""
    assert mode == "native-sfx", mode
    assert "app.settings" not in sys.modules, "prepare before application imports/startup"
    root = Path(root)
    data = Path(fixtures.DB).parent
    assert data.resolve().is_relative_to(Path(fixtures.DATA).resolve())
    assert not (data / "sfx_plan.json").exists(), "fresh SFX output directory required"
    assert not (data / "sfx").exists(), "preexisting SFX files cannot prove generation"
    assert not fixtures.FixtureHandler.counts, "fresh existing fixture handler required"
    plan = {"events": [
        {"frame_number": 1, "t_start": 0.5, "duration": 0.8, "kind": "whoosh",
         "prompt": "soft paper whoosh", "gain": 0.2, "duck": True},
        {"frame_number": 2, "t_start": 3.5, "duration": 0.6, "kind": "hit",
         "prompt": "light desk hit", "gain": 0.5, "duck": False},
    ]}
    keys = (PLAN, GEN, *BOUNDARY)
    types = ("sfx_plan", "sfx_gen", "script", "music", "publish")
    nodes = [
        {"id": key, "type": typ, "position": {"x": 100 + index * 250, "y": 100},
         "data": {"label": "Synthetic " + typ,
                  "groupId": "native-sfx" if key in SCOPE else "outside",
                  **({"modelId": "gpt-kie"} if key == PLAN else {})}}
        for index, (key, typ) in enumerate(zip(keys, types))
    ]
    edges = [{"id": "native-sfx-edge-" + str(index), "source": source,
              "target": target, "data": {"kind": "after"}}
             for index, (source, target) in enumerate(zip(keys, keys[1:]))]
    replicas = {}
    for name, path in (("master", fixtures.DATA / "state.db"), ("project", fixtures.DB)):
        before = _snapshot(path)
        assert not before["frames"], "fresh synthetic seed must have no source frames"
        with sqlite3.connect(path) as db:
            meta = before["project"]["meta"]
            graph = dict(meta.get("canvas_graph") or {})
            graph.update(nodes=nodes, edges=edges)
            meta["canvas_graph"] = graph
            for key in ("active_excel_gpt_node_key", "step_failure", "user_stop",
                        "auto_await_manual_start", "sfx_plan", "sfx_ready", "sfx_generated"):
                meta.pop(key, None)
            jobs = dict(meta.get("ai_jobs") or {})
            jobs.pop("sfx_plan", None)
            jobs.pop("sfx_files", None)
            meta["ai_jobs"] = jobs
            db.execute("UPDATE projects SET status='new',auto_mode=0,meta=? WHERE id=2",
                       (json.dumps(meta, ensure_ascii=False),))
            run_id = before["run"]["id"]
            db.execute("UPDATE workflow_runs SET nodes_snapshot=?,edges_snapshot=? WHERE id=?",
                       (json.dumps(nodes), json.dumps(edges), run_id))
            db.execute("DELETE FROM node_runs WHERE workflow_run_id=?", (run_id,))
            for node in nodes:
                db.execute("INSERT INTO node_runs(workflow_run_id,node_key,node_type,status,"
                           "progress,attempts,meta,created_at,updated_at) VALUES(?,?,?,'pending',"
                           "0,0,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                           (run_id, node["id"], node["type"]))
            for number, uid in enumerate(("af" * 16, "bf" * 16), 1):
                db.execute("INSERT INTO frames(project_id,number,uuid,sort_key,voiceover_text,"
                           "duration_seconds,status,attrs,created_at,updated_at) "
                           "VALUES(2,?,?,?,?,3.0,'planned','{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                           (number, uid, number, "Synthetic SFX voiceover " + str(number)))
        initial = _snapshot(path)
        assert all(node["status"] == "pending" and node["attempts"] == 0
                   for node in initial["nodes"].values())
        assert initial["artifacts"] == before["artifacts"], "retain original Artifact identities"
        replicas[name] = initial

    def answer(node, attempt):
        assert node == PLAN and attempt == 1, "only one native planner JSON response allowed"
        return json.dumps(plan, ensure_ascii=False)

    fixtures.NODES = (PLAN, GEN, OUTSIDE)
    fixtures.synthetic_answer = answer
    # These are synthetic parent-container settings, established before imports.
    os.environ["SFX_ENABLED"] = "true"
    os.environ["ELEVENLABS_API_KEY"] = ""
    prepared = {
        "scenario": mode, "scope": list(SCOPE),
        "outside": list(BOUNDARY), "plan_payload": plan,
        "initial_replicas": replicas,
        "worker_constraints": {
            "observation_mode": "observe", "managed_ready": False,
            "auto_mode": False, "ordinary_api_starts": 2,
            "ordinary_api_stops": 0,
            "automatic_continuation_verified": False,
            "queue_admission": "ordinary step API uses original explicit-UI start; worker queue must admit project 2",
            "native_transport": "existing loopback JSON fixture for plan; Python WAV for generation",
            "media_tools": "parent diagnostic image; original ffprobe/RMS helpers are not patched",
            "mix_verified": False,
            "gain_duck": "persisted native event metadata; montage application is outside this scope",
            "cached_rejection": {
                "worker_verified": False,
                "coverage": "tests/observe_v1/test_native_sfx_plan_checkpoint.py; parent owns execution",
                "blocker": "ordinary sfx_plan API start falls back to _wipe_sfx_plan even with force_wipe=false",
                "required_seam": "original worker checkpoint resume preserving persisted input; pending",
            },
            "application_shutdown": "parent group_acceptance finally block",
        },
        "interventions": [
            "fresh copied synthetic graph and two three-second Frame inputs before startup",
            "all original NodeRuns pending; no output files or successful statuses injected",
            "SFX enabled and ElevenLabs key empty before app imports",
            "ordinary API starts only; auto_mode false keeps active outside/music/publish pending",
            "one synthetic external JSON plan response",
        ],
    }
    (root / "native-sfx-prepared.json").write_text(
        json.dumps(prepared, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return prepared


def _state(fixtures):
    return {"master": _snapshot(fixtures.DATA / "state.db"), "project": _snapshot(fixtures.DB)}


def _assert_inputs_and_boundary(state, prepared):
    for name, current in state.items():
        initial = prepared["initial_replicas"][name]
        assert current["frames"] == initial["frames"], (name, "original Frame inputs changed")
        assert current["artifacts"] == initial["artifacts"], (name, "unexpected Artifact identities")
        assert current["run"] == initial["run"], (name, "immutable graph changed")
        assert not current["project"]["auto_mode"], "ordinary scope is not an automatic boundary"
        for key in BOUNDARY:
            node = current["nodes"][key]
            assert node["status"] == "pending" and node["attempts"] == 0, (name, key, node)


async def _history(client, prefix):
    events = []
    after = 0
    while True:
        response = await client.get(prefix + "/events", params={"after": after, "limit": 500})
        response.raise_for_status()
        page = response.json()["events"]
        if not page:
            return events
        events.extend(page)
        next_after = page[-1]["id"]
        assert next_after > after, "event cursor must advance"
        after = next_after


async def _wait_ready(fixtures, observer, prepared, key, status, timeout_s):
    from app.services.step_cancel import is_generation_active

    assert timeout_s > 0
    state = _state(fixtures)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await observer.catch_up()
        state = _state(fixtures)
        _assert_inputs_and_boundary(state, prepared)
        if all(replica["nodes"][key]["status"] == "done"
               and replica["project"]["status"] == status for replica in state.values()) \
                and not is_generation_active(2):
            return state
        assert not any(replica["nodes"][key]["status"] == "failed" for replica in state.values()), state
        await asyncio.sleep(0.25)
    raise AssertionError("native worker did not reach " + status + ": " + repr(state))


async def _file_proof(path, event, frame):
    from app.services.ai_result_io import audio_rms_db, ffprobe_duration

    assert path.is_file() and path.stat().st_size >= 1000, path
    with wave.open(str(path), "rb") as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) == (1, 2, 44100)
        sample_count = audio.getnframes()
        pcm = audio.readframes(sample_count)
        assert len(pcm) == sample_count * 2 and sample_count > 0
        wav_duration = sample_count / audio.getframerate()
    square_sum = sum(sample[0] ** 2 for sample in struct.iter_unpack("<h", pcm))
    assert square_sum > 0, "actual WAV samples must not be silent"
    pcm_rms = 20 * math.log10(math.sqrt(square_sum / sample_count) / 32768)
    assert math.isfinite(pcm_rms) and pcm_rms >= -60.0, pcm_rms
    probe_duration = await asyncio.to_thread(ffprobe_duration, path)
    native_rms = await asyncio.to_thread(audio_rms_db, path)
    assert probe_duration is not None and math.isfinite(probe_duration)
    assert abs(probe_duration - event["duration"]) <= 0.6, probe_duration
    assert abs(wav_duration - event["duration"]) <= 1 / 44100, wav_duration
    assert abs(probe_duration - wav_duration) <= 0.01
    if native_rms is not None:
        assert math.isfinite(native_rms) and native_rms >= -60.0, native_rms
    return {
        "path": str(path), "bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "wav_duration": wav_duration, "ffprobe_duration": probe_duration,
        "pcm_rms_db": pcm_rms, "rms_source": "independent actual 16-bit PCM samples",
        "native_rms_db": native_rms, "native_rms_confirmed": native_rms is not None,
        "source_frame": {key: frame[key] for key in ("id", "project_id", "uuid", "number")},
        "artifact_row": None,
        "native_storage": "project.meta.ai_jobs.sfx_files; native SFX creates no Artifact row",
    }


async def run(client, fixtures, observer, root, result, prepared, *, browser=False, timeout_s=120):
    """Observe original workers and verify their outputs without managed controls."""
    from app.services.step_cancel import is_generation_active
    from app.settings import settings

    assert not browser, "this helper has no separate browser acceptance flow"
    assert timeout_s > 0
    assert settings.sfx_enabled and not settings.elevenlabs_api_key
    assert not is_generation_active(2), "fresh original worker must be idle before the first API start"
    root = Path(root)
    base = "http://127.0.0.1:18765/api/projects/2"
    state = _state(fixtures)
    _assert_inputs_and_boundary(state, prepared)
    assert all(replica["nodes"][key]["status"] == "pending"
               for replica in state.values() for key in SCOPE), state
    assert not (Path(fixtures.DB).parent / "sfx_plan.json").exists()
    assert not (Path(fixtures.DB).parent / "sfx").exists()
    response = await client.post(base + "/observations", json={"node_keys": list(SCOPE)})
    response.raise_for_status()
    scope = response.json()
    assert scope["mode"] == "observe", scope
    execution = scope["execution_id"]
    prefix = base + "/observations/" + execution
    result.update(execution_id=execution, operator_starts=0, operator_stops=0, observation_only=True,
                  managed_ready=False, semantic_quality_verified=False,
                  worker_constraints=prepared["worker_constraints"],
                  interventions=prepared["interventions"],
                  scope_boundary="auto_mode false; parent cancels after observed native boundary")

    async def start(key, step):
        response = await client.post(base + "/steps/" + step + "/run",
                                     params={"node_key": key, "force_wipe": "false"})
        response.raise_for_status()
        result["operator_starts"] += 1

    await start(PLAN, "sfx_plan")
    await _wait_ready(fixtures, observer, prepared, PLAN, "sfx_plan_ready", timeout_s)
    await start(GEN, "sfx_gen")
    state = await _wait_ready(fixtures, observer, prepared, GEN, "sfx_ready", timeout_s)
    await observer.catch_up()
    history = await _history(client, prefix)
    counts = dict(fixtures.FixtureHandler.counts)
    assert counts == {PLAN: 1}, counts
    for key in SCOPE:
        starts = [event for event in history if event["node_key"] == key and event["kind"] == "node_start"]
        ends = [event for event in history if event["node_key"] == key and event["kind"] == "node_end"]
        assert len(starts) == len(ends) == 1, (key, starts, ends)
        assert starts[0]["attempt_id"] == ends[0]["attempt_id"]
    model_starts = [event for event in history if event["kind"] == "model_start"]
    model_ends = [event for event in history if event["kind"] == "model_end"]
    assert len(model_starts) == len(model_ends) == 1
    assert model_starts[0]["node_key"] == model_ends[0]["node_key"] == PLAN
    assert model_starts[0]["request_id"] == model_ends[0]["request_id"]
    assert model_ends[0]["payload"]["complete"] is True
    assert model_starts[0]["payload"]["host"] in {"127.0.0.1", "localhost"}
    assert not any(event["kind"] in {"node_error", "model_error"} for event in history)
    data = Path(fixtures.DB).parent
    plan_output = json.loads((data / "sfx_plan.json").read_text(encoding="utf-8"))
    assert plan_output == {"total_duration": 6.0, "events": prepared["plan_payload"]["events"]}
    meta = state["project"]["project"]["meta"]
    assert meta["sfx_plan"] and meta["sfx_ready"] and meta["sfx_generated"]
    assert meta["ai_jobs"]["sfx_plan"] == prepared["plan_payload"]
    records = meta["ai_jobs"]["sfx_files"]["files"]
    assert len(records) == 2 and [record["idx"] for record in records] == [0, 1]
    for replica in state.values():
        replica_meta = replica["project"]["meta"]
        assert replica_meta["sfx_plan"] and replica_meta["sfx_ready"] and replica_meta["sfx_generated"]
        assert replica_meta["ai_jobs"]["sfx_plan"] == prepared["plan_payload"]
        assert replica_meta["ai_jobs"]["sfx_files"]["files"] == records
    frames = {frame["number"]: frame for frame in state["project"]["frames"]}
    proofs = []
    for record, event in zip(records, prepared["plan_payload"]["events"]):
        assert record["provider"] == "local_synth", record
        for key in ("frame_number", "t_start", "duration", "kind", "gain", "duck"):
            assert record[key] == event[key], (key, record, event)
        path = Path(record["path"]).resolve()
        assert path == (data / "sfx" / ("sfx_%02d_%s.wav" % (record["idx"], event["kind"]))).resolve()
        frame_start = sum(frame["duration_seconds"] for number, frame in frames.items()
                          if number < event["frame_number"])
        assert frame_start <= event["t_start"] < frame_start + frames[event["frame_number"]]["duration_seconds"]
        proofs.append(await _file_proof(path, event, frames[event["frame_number"]]))
    assert {path.resolve() for path in (data / "sfx").iterdir()} == {
        Path(record["path"]).resolve() for record in records
    }
    result.update(native_sfx_execution_observed=True, native_sfx_files=proofs,
                  native_sfx_state=state, artifact_rows_created=0,
                  native_sfx_cached_rejection_worker_verified=False,
                  native_sfx_cached_rejection=prepared["worker_constraints"]["cached_rejection"],
                  native_sfx_plan={"path": str(data / "sfx_plan.json"),
                                   "sha256": hashlib.sha256((data / "sfx_plan.json").read_bytes()).hexdigest()},
                  native_rms_verification_complete=all(proof["native_rms_confirmed"] for proof in proofs))

    response = await client.get(prefix + "/actions")
    response.raise_for_status()
    controls = response.json()
    assert not controls["configured"] and not controls["enabled"] and not controls["records"] and not controls["recoveries"], controls
    await observer.catch_up()
    state = _state(fixtures)
    _assert_inputs_and_boundary(state, prepared)
    assert not is_generation_active(2), "native worker still active at acceptance boundary"
    history = await _history(client, prefix)
    assert all(replica["nodes"][key]["status"] == "done"
               for replica in state.values() for key in SCOPE), state
    assert all(replica["project"]["status"] == "sfx_ready" for replica in state.values()), state
    result["native_sfx_state"] = state
    assert not any(event["node_key"] in BOUNDARY for event in history), history
    (root / "native-sfx-history.json").write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    result.update(accepted=True, acceptance_level="synthetic native SFX observation only",
                  automatic_continuation_verified=False, managed_ready=False,
                  native_sfx_fixture_counts=dict(fixtures.FixtureHandler.counts))
