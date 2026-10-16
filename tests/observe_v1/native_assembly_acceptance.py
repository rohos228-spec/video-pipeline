from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import shutil
import sqlite3
import struct
import subprocess
import time
import uuid
import wave
from pathlib import Path


NODE_KEY = "native-assemble-A"
OUTSIDE = "outside-native-publish-Z"
SLOTS = ((1, "red", 440, 2.0), (2, "blue", 880, 3.0))


def _local(path):
    assert Path("/.dockerenv").is_file(), "native assembly acceptance requires Docker"
    resolved = Path(path).resolve()
    assert resolved.is_relative_to(Path("/results")), "fixture path must stay in /results"
    return resolved


def _tools():
    _local("/results")
    tools = {name: shutil.which(name) for name in ("ffmpeg", "ffprobe")}
    assert all(tools.values()), "diagnostic image requires ffmpeg AND ffprobe; no installation attempted"
    return tools


def _save(path, body):
    _local(path).write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


def _failure(exc):
    stderr = getattr(exc, "stderr", None)
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    return {"type": type(exc).__name__, "reason": str(exc), "stderr": (stderr or "")[-8000:]}


def _probe(path):
    path = _local(path)
    probe = subprocess.run(
        [_tools()["ffprobe"], "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, timeout=20,
    )
    return json.loads(probe.stdout)


def _duration(probe):
    duration = float(probe["format"]["duration"])
    assert math.isfinite(duration) and duration > 0, probe
    return duration


def _generate_inputs(project_dir):
    tools = _tools()
    inputs = _local(project_dir / "assembly-fixture-inputs")
    inputs.mkdir(exist_ok=False)
    for name in ("audio", "videos", "final", "music"):
        folder = _local(project_dir / name)
        assert not folder.exists() or not any(folder.iterdir()), "unexpected seeded media: " + str(folder)
        folder.mkdir(exist_ok=True)
    files = []
    mp3s = []
    for number, color, frequency, seconds in SLOTS:
        video = project_dir / "videos" / f"clip_{number:03d}_fixture.mp4"
        subprocess.run(
            [tools["ffmpeg"], "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", f"color=c={color}:s=160x240:r=25:d=4",
             "-an", "-c:v", "libx264", "-threads", "1", "-pix_fmt", "yuv420p", str(video)],
            check=True, capture_output=True, timeout=45,
        )
        wav = inputs / f"frame_{number:03d}.wav"
        samples = int(44100 * seconds)
        pcm = b"".join(struct.pack("<h", round(10000 * math.sin(2 * math.pi * frequency * i / 44100)))
                       for i in range(samples))
        with wave.open(str(wav), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(44100)
            audio.writeframes(pcm)
        mp3 = project_dir / "audio" / f"frame_{number:03d}.mp3"
        subprocess.run(
            [tools["ffmpeg"], "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
             "-i", str(wav), "-map", "0:a:0", "-c:a", "libmp3lame", "-b:a", "128k", str(mp3)],
            check=True, capture_output=True, timeout=45,
        )
        mp3s.append(mp3)
        for path, purpose in ((video, "scene_video_input"), (wav, "PCM_source_fixture"), (mp3, "per_frame_audio_input")):
            probe = _probe(path)
            files.append({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                          "purpose": purpose, "frame_number": number, "color": color,
                          "tone_hz": frequency, "source_pcm_seconds": seconds, "probe": probe})
        assert [s["codec_type"] for s in _probe(video)["streams"]] == ["video"]
        assert abs(_duration(_probe(mp3)) - seconds) < .1, "fixture encoder padding exceeded bound"
    concat = inputs / "frame-audio-concat.txt"
    concat.write_text("".join(f"file '{path}'\n" for path in mp3s), encoding="utf-8")
    voice = project_dir / "audio" / "voice_full_fixture.mp3"
    subprocess.run(
        [tools["ffmpeg"], "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "concat", "-safe", "0", "-i", str(concat), "-map", "0:a:0", "-c:a", "copy", str(voice)],
        check=True, capture_output=True, timeout=45,
    )
    files.append({"path": str(voice), "purpose": "full_audio_input", "probe": _probe(voice),
                  "sha256": hashlib.sha256(voice.read_bytes()).hexdigest(),
                  "construction": "fixed concat in frame-number order of locally encoded PCM tones"})
    return files, voice


def prepare(fixtures, root, mode):
    root = _local(root)
    try:
        assert mode == "launch-assembly", "parent runner must explicitly select launch-assembly"
        assert fixtures.DATA.resolve() == root / "free-run-data"
        project_dir = _local(fixtures.DB.parent)
        assert project_dir == fixtures.DATA / "videos" / fixtures.SLUG
        paths = (fixtures.DATA / "state.db", fixtures.DB)
        for path in paths:
            assert path.is_file(), "copied seed DB missing: " + str(path)
            with sqlite3.connect(path) as db:
                assert db.execute("SELECT count(*) FROM frames WHERE project_id=2").fetchone()[0] == 0
                assert db.execute("SELECT count(*) FROM hitl_requests WHERE project_id=2").fetchone()[0] == 0
                assert db.execute("SELECT count(*) FROM artifacts WHERE project_id=2 AND kind IN "
                                  "('audio','scene_video','final_video','subtitle','whisper_words','music')").fetchone()[0] == 0
                assert db.execute("SELECT count(*) FROM workflow_runs WHERE project_id=2").fetchone()[0] == 1
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if "action_execution_controls" in tables:
                    assert not db.execute("SELECT 1 FROM action_execution_controls WHERE project_id=2").fetchone()
        files, voice = _generate_inputs(project_dir)
        nodes = [
            {"id": NODE_KEY, "type": "assemble", "position": {"x": 100, "y": 100},
             "data": {"label": "Synthetic native assembly"}},
            {"id": OUTSIDE, "type": "publish", "position": {"x": 500, "y": 100},
             "data": {"label": "Outside final approval boundary"}},
        ]
        edges = [{"id": "native-assembly-edge", "source": NODE_KEY, "target": OUTSIDE,
                  "data": {"kind": "after"}}]
        provenance = {"synthetic_only": True, "origin": "native_assembly_acceptance.prepare before startup",
                      "provider_generated": False, "human_approved": False}
        for path in paths:
            with sqlite3.connect(path) as db:
                meta = json.loads(db.execute("SELECT meta FROM projects WHERE id=2").fetchone()[0])
                meta["canvas_graph"].update(nodes=nodes, edges=edges)
                meta.pop("active_excel_gpt_node_key", None)
                meta.pop("step_failure", None)
                meta["bgm_enabled"] = False
                meta.setdefault("node_step_params", {})["assemble"] = {
                    "subtitles_enabled": False, "send_to_main_pc": False,
                    "post_voiceover_tail_seconds": 0, "skip_intro_enabled": False, "skip_intro_seconds": 0,
                }
                assert not (meta.get("ai_jobs") or {}).get("sfx_files"), "seed contains unrelated SFX"
                db.execute("UPDATE projects SET status='new',auto_mode=0,meta=? WHERE id=2", (json.dumps(meta),))
                for number, color, frequency, seconds in SLOTS:
                    frame = db.execute(
                        "INSERT INTO frames(project_id,number,voiceover_text,status,attrs,uuid,sort_key,created_at,updated_at) "
                        "VALUES(2,?,?,'planned',?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                        (number, f"Synthetic {color} frame with {frequency} Hz tone.", json.dumps(provenance),
                         uuid.uuid4().hex, number),
                    ).lastrowid
                    db.execute(
                        "INSERT INTO artifacts(project_id,frame_id,kind,uuid,path,meta,created_at) "
                        "VALUES(2,?,'scene_video',?,?,?,CURRENT_TIMESTAMP)",
                        (frame, uuid.uuid4().hex, str(project_dir / "videos" / f"clip_{number:03d}_fixture.mp4"),
                         json.dumps({**provenance, "shot": 1})),
                    )
                db.execute("INSERT INTO artifacts(project_id,kind,uuid,path,meta,created_at) "
                           "VALUES(2,'audio',?,?,?,CURRENT_TIMESTAMP)",
                           (uuid.uuid4().hex, str(voice), json.dumps({**provenance, "mode": "per_frame",
                                                                   "source": "diagnostic_pcm_fixture"})))
                run_id = db.execute("SELECT id FROM workflow_runs WHERE project_id=2").fetchone()[0]
                db.execute("UPDATE workflow_runs SET nodes_snapshot=?,edges_snapshot=? WHERE id=?",
                           (json.dumps(nodes), json.dumps(edges), run_id))
                db.execute("DELETE FROM node_runs WHERE workflow_run_id=?", (run_id,))
                for node in nodes:
                    db.execute("INSERT INTO node_runs(workflow_run_id,node_key,node_type,status,progress,attempts,meta,created_at,updated_at) "
                               "VALUES(?,?,?,'pending',0,0,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                               (run_id, node["id"], node["type"]))
        cfg = {"version": 1, "mode": "synthetic", "online_enabled": False, "endpoint": None,
               "decision": None, "profiles": {}, "allowed_actions": []}
        config = root / "synthetic-native-assembly-runtime.json"
        _save(config, cfg)
        os.environ["ORCHESTRATOR_RUNTIME_CONFIG"] = str(config)
        fixtures.NODES = (NODE_KEY, OUTSIDE)

        def forbid_model(node, attempt):
            raise AssertionError(f"native assembly must not call a model: {node}, attempt {attempt}")

        fixtures.synthetic_answer = forbid_model
        prepared = {"mode": mode, "node_key": NODE_KEY, "outside": OUTSIDE, "tools": _tools(),
                    "input_files": files, "full_audio": str(voice), "subtitles_enabled": False,
                    "initial_auto_mode": False, "managed_adapter_accepted": False,
                    "asr_accepted": False, "subtitles_accepted": False,
                    "interventions": ["copied synthetic seed only; two input frames/videos and per-frame PCM/MP3 before startup",
                                      "pending NodeRuns and NULL frame timing; no final output or done/approved status seeded",
                                      "explicit initial subtitles=false, BGM=false, tail=0, intro=0 and auto_mode=false",
                                      "one original step API start; no direct executor call, HITL decision, recovery or publish start"]}
        prepared["initial_state"] = _state(fixtures.DB)
        assert all(f["start_ts"] is None and f["end_ts"] is None and f["duration_seconds"] is None
                   for f in prepared["initial_state"]["frames"])
        with sqlite3.connect(fixtures.DATA / "state.db") as db:
            assert db.execute("SELECT status FROM projects WHERE id=1").fetchone()[0] == "new"
        _save(root / "native-assembly-prepared.json", prepared)
        return prepared
    except Exception as exc:
        _save(root / "native-assembly-prepare-failure.json", {"accepted": False, "failure": _failure(exc)})
        raise


def _state(path):
    with sqlite3.connect(_local(path)) as db:
        db.row_factory = sqlite3.Row
        project = dict(db.execute("SELECT status,auto_mode,meta FROM projects WHERE id=2").fetchone())
        project["meta"] = json.loads(project["meta"])
        nodes = {r["node_key"]: dict(r) for r in db.execute(
            "SELECT node_key,status,attempts,error FROM node_runs WHERE workflow_run_id IN "
            "(SELECT id FROM workflow_runs WHERE project_id=2)")}
        frames = [dict(r) for r in db.execute("SELECT number,voiceover_text,start_ts,end_ts,duration_seconds,status "
                                             "FROM frames WHERE project_id=2 ORDER BY number")]
        artifacts = [dict(r) for r in db.execute("SELECT id,kind,uuid,path,meta,approved_at FROM artifacts WHERE project_id=2 ORDER BY id")]
        hitl = [dict(r) for r in db.execute("SELECT id,kind,decision,decided_at,payload FROM hitl_requests WHERE project_id=2 ORDER BY id")]
        for row in artifacts:
            row["meta"] = json.loads(row["meta"])
        for row in hitl:
            row["payload"] = json.loads(row["payload"])
        return {"project": project, "nodes": nodes, "frames": frames, "artifacts": artifacts, "hitl": hitl}


def _assert_boundary(state):
    assert state["project"]["status"] == "assembled", state
    assert not state["project"]["auto_mode"]
    assert not (state["project"]["meta"].get("published_platforms") or []), "publication side effect"
    assert state["nodes"][NODE_KEY]["status"] == "done", state["nodes"]
    assert state["nodes"][OUTSIDE]["status"] == "pending" and state["nodes"][OUTSIDE]["attempts"] == 0, state["nodes"]
    assert len(state["hitl"]) == 1, state["hitl"]
    approval = state["hitl"][0]
    assert approval["kind"] == "approve_final" and approval["decision"] == "pending" and approval["decided_at"] is None, approval
    finals = [a for a in state["artifacts"] if a["kind"] == "final_video"]
    assert len(finals) == 1 and finals[0]["approved_at"] is None, finals
    assert approval["payload"]["step"] == "final" and approval["payload"]["video_path"] == finals[0]["path"], approval
    assert not [a for a in state["artifacts"] if a["kind"] in {"whisper_words", "subtitle"}]
    assert state["project"]["meta"]["node_step_params"]["assemble"]["subtitles_enabled"] is False
    return finals[0]


def _rgb(path, at):
    decoded = subprocess.run(
        [_tools()["ffmpeg"], "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(_local(path)),
         "-ss", str(at), "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=1:1",
         "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
        check=True, capture_output=True, timeout=20,
    )
    assert len(decoded.stdout) == 3, "one RGB pixel must decode"
    return list(decoded.stdout)


def _tone(path, at):
    decoded = subprocess.run(
        [_tools()["ffmpeg"], "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(_local(path)),
         "-ss", str(at), "-t", "0.10", "-map", "0:a:0", "-ac", "1", "-ar", "8000",
         "-c:a", "pcm_s16le", "-f", "s16le", "pipe:1"],
        check=True, capture_output=True, timeout=20,
    )
    pcm = decoded.stdout
    assert len(pcm) >= 1440 and len(pcm) % 2 == 0, "100-ms audio must decode"
    samples = struct.unpack("<" + "h" * (len(pcm) // 2), pcm)
    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    crossings = sum(a <= 0 < b for a, b in zip(samples, samples[1:]))
    return {"rms": rms, "hz": crossings * 8000 / (len(samples) - 1)}


def _verify_media(state, prepared, project_dir, baseline):
    artifact = _assert_boundary(state)
    final = _local(artifact["path"])
    assert final == project_dir / "final" / f"{project_dir.name}.mp4" and final.is_file(), artifact
    assert artifact["uuid"] not in {a["uuid"] for a in baseline["artifacts"]}
    for source in prepared["input_files"]:
        if source["purpose"] != "full_audio_input":
            assert hashlib.sha256(_local(source["path"]).read_bytes()).hexdigest() == source["sha256"], source
    probe = _probe(final)
    assert sorted(s["codec_type"] for s in probe["streams"]) == ["audio", "video"], probe
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    assert (video["codec_name"], video["width"], video["height"]) == ("h264", 160, 240), video
    assert audio["codec_name"] == "aac", audio
    per_frame = [round(_duration(_probe(project_dir / "audio" / f"frame_{n:03d}.mp3")), 3) for n, *_ in SLOTS]
    master = _duration(_probe(prepared["full_audio"]))
    assert abs(master - sum(per_frame)) <= .15, "full audio must represent both fixture slots"
    frames = state["frames"]
    assert [f["number"] for f in frames] == [1, 2], frames
    assert [f["voiceover_text"] for f in frames] == [f["voiceover_text"] for f in baseline["frames"]]
    boundary = per_frame[0]
    for frame, start, end in ((frames[0], 0., boundary), (frames[1], boundary, master)):
        assert abs(frame["start_ts"] - start) <= .01 and abs(frame["end_ts"] - end) <= .02, frame
        assert abs(frame["duration_seconds"] - (end - start)) <= .02, frame
    assert abs(_duration(probe) - master) <= .15, "final duration must follow real per-frame audio"
    assert abs(float(video["duration"]) - master) <= .15 and abs(float(audio["duration"]) - master) <= .15, probe
    evidence = []
    for at, color, hz in ((boundary / 2, "red", 440), (boundary - .25, "red", 440),
                          (boundary + .25, "blue", 880), ((boundary + master) / 2, "blue", 880)):
        rgb = _rgb(final, round(at, 3))
        tone = _tone(final, round(at, 3))
        evidence.append({"at_seconds": at, "expected_color": color, "rgb": rgb, "expected_hz": hz, "audio": tone})
        assert (rgb[0] > rgb[2] + 100 if color == "red" else rgb[2] > rgb[0] + 100), evidence
        assert tone["rms"] > 1000 and abs(tone["hz"] - hz) < 35, evidence
    return {"path": str(final), "sha256": hashlib.sha256(final.read_bytes()).hexdigest(), "probe": probe,
            "full_audio_seconds": master, "per_frame_mp3_seconds": per_frame, "decoded_samples": evidence,
            "duration_tolerance_seconds": .15, "frame_timing_tolerance_seconds": .02}


async def _history(client, prefix):
    history = []
    after = 0
    while True:
        response = await client.get(prefix + "/events", params={"after": after, "limit": 500})
        response.raise_for_status()
        page = response.json()
        history.extend(page["events"])
        if not page["has_more"]:
            return history
        assert page["next_cursor"] > after, "history cursor must advance"
        after = page["next_cursor"]


async def run(client, fixtures, observer, root, result, prepared, *, browser=False):
    root = _local(root)
    result.update(accepted=False, interventions=prepared["interventions"], operator_starts=0,
                  managed_adapter_accepted=False, asr_accepted=False, subtitles_accepted=False,
                  automatic_final_review_accepted=False, publication_accepted=False,
                  native_assembly_stage="initial_state",
                  db_authority={"content": "project.db", "master_projection": "status/NodeRuns"})
    try:
        assert not browser, "this draft uses one original API start, not a browser operator"
        assert not dict(fixtures.FixtureHandler.counts), "fresh loopback fixture must have zero calls"
        initial = _state(fixtures.DB)
        assert all(n["status"] == "pending" and n["attempts"] == 0 for n in initial["nodes"].values()), initial
        assert initial["project"]["status"] not in {"assembling", "assembled", "publishing", "published"}, initial
        assert not initial["hitl"], initial
        assert not [a for a in initial["artifacts"] if a["kind"] == "final_video"]
        result["baseline_after_startup"] = initial
        with sqlite3.connect(fixtures.DATA / "state.db") as db:
            outside_project_status = db.execute("SELECT status FROM projects WHERE id=1").fetchone()[0]
        base = "http://127.0.0.1:18765/api/projects/2"
        response = await client.post(base + "/observations", json={"node_keys": [NODE_KEY, OUTSIDE]})
        assert response.status_code == 201, (response.status_code, response.text)
        scope = response.json()
        assert scope["mode"] == "observe" and set(scope["node_keys"]) == {NODE_KEY, OUTSIDE}, scope
        prefix = base + "/observations/" + scope["execution_id"]
        result.update(execution_id=scope["execution_id"], observation_scope=scope,
                      scope_boundary="original final-video HITL; ordinary native API, auto_mode=false",
                      native_assembly_stage="original_step_api_start")
        response = await client.post(base + "/steps/assemble/run", params={"node_key": NODE_KEY, "mode": "resume"})
        assert response.status_code == 200, (response.status_code, response.text)
        result["operator_starts"] = 1
        result["native_assembly_stage"] = "original_worker_and_media_verification"
        deadline = time.monotonic() + 120
        while True:
            state = _state(fixtures.DB)
            result["native_state"] = state
            if state["nodes"][NODE_KEY]["status"] == "failed" or (
                state["project"]["status"] == "assembled" and state["hitl"] and state["nodes"][NODE_KEY]["status"] == "done"
            ) or time.monotonic() >= deadline:
                break
            await asyncio.sleep(.25)
        media = await asyncio.to_thread(_verify_media, state, prepared, _local(fixtures.DB.parent), initial)
        result["media_verification"] = media
        _save(root / "native-assembly-media-verification.json", media)
        result["native_assembly_stage"] = "pending_final_approval_stability"
        await asyncio.sleep(12)
        after = _state(fixtures.DB)
        _assert_boundary(after)
        assert after["nodes"] == state["nodes"] and after["frames"] == state["frames"]
        assert after["artifacts"] == state["artifacts"] and after["hitl"] == state["hitl"]
        assert hashlib.sha256(Path(media["path"]).read_bytes()).hexdigest() == media["sha256"], "final output changed while awaiting approval"
        result["native_assembly_stage"] = "committed_observation_history"
        await observer.catch_up()
        history = await _history(client, prefix)
        _save(root / "api-history.json", {"events": history})
        facts = [e for e in history if e["node_key"] == NODE_KEY]
        starts = [e for e in facts if e["kind"] == "node_start"]
        ends = [e for e in facts if e["kind"] == "node_end"]
        assert len(starts) == len(ends) == 1, facts
        assert starts[0]["attempt_id"] and starts[0]["attempt_id"] == ends[0]["attempt_id"]
        assert ends[0]["payload"]["project_status"] == "assembled" and ends[0]["payload"]["worker_returned"] is True
        committed = [e for e in facts if e["kind"] == "node_change" and e.get("db_scope") == "project"
                     and e["payload"].get("transactional") is True
                     and e["payload"].get("changes", {}).get("status", {}).get("after") == ["done"]]
        assert committed, "original NodeRun completion must be delivered from the project transaction outbox"
        assert any(t.get("initiator") == "worker" and t.get("to") == "done"
                   for e in committed for t in e["payload"].get("transitions", [])), committed
        assert not [e for e in history if e["kind"] in {"model_start", "model_error", "node_error"}], history
        assert not [e for e in history if e["node_key"] == OUTSIDE and e["kind"] in {"node_start", "node_end", "model_start"}], history
        result["native_assembly_stage"] = "original_final_hitl_api_visibility"
        response = await client.get("http://127.0.0.1:18765/api/hitl/project/2")
        response.raise_for_status()
        api_hitl = response.json()
        result["final_hitl_api"] = {"rows": api_hitl, "project_db_rows": after["hitl"],
                                    "existing_route_db": "master", "existing_worker_db": "project"}
        assert len(api_hitl) == 1 and api_hitl[0]["id"] == after["hitl"][0]["id"], (
            "original final HITL exists in project.db but must also be visible through the original HITL API")
        assert api_hitl[0]["kind"] == "approve_final" and api_hitl[0]["decision"] == "pending" and api_hitl[0]["decided_at"] is None
        assert api_hitl[0]["payload"]["video_path"] == media["path"]
        response = await client.get(base)
        response.raise_for_status()
        assert response.json()["status"] == "assembled", response.json()
        actions_response = await client.get(prefix + "/actions")
        actions_response.raise_for_status()
        actions = actions_response.json()
        assert not actions["configured"] and not actions["enabled"] and actions["requests_used"] == 0
        assert not actions["records"] and not actions["recoveries"], actions
        decisions_response = await client.get(prefix + "/decisions")
        decisions_response.raise_for_status()
        assert not decisions_response.json()["records"], decisions_response.json()
        assert not dict(fixtures.FixtureHandler.counts), "assembly or final approval unexpectedly called a model/provider"
        with sqlite3.connect(fixtures.DATA / "state.db") as db:
            assert db.execute("SELECT status FROM projects WHERE id=1").fetchone()[0] == outside_project_status
        master = _state(fixtures.DATA / "state.db")
        assert master["project"]["status"] == "assembled", master
        assert master["nodes"][NODE_KEY]["status"] == "done" and master["nodes"][OUTSIDE]["status"] == "pending", master
        result.update(accepted=True, native_assembly_accepted=True, native_assembly_stage="accepted", final_approval=api_hitl[0], actions=actions,
                      outside_publish_preserved=True, provider_calls=0, semantic_quality_verified=False,
                      final_hitl_observation_fact_present=any(e["kind"] == "human_approval_pending" for e in facts),
                      native_state=after)
        _save(root / "native-assembly-acceptance.json", result)
    except Exception as exc:
        result.update(accepted=False, native_assembly_accepted=False, native_assembly_failure=_failure(exc))
        _save(root / "native-assembly-acceptance.json", result)
        raise
