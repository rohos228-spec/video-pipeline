"""Original video API/worker acceptance using the existing group runner.

Parent wires mode ``native-video`` to prepare/handler_for/run, before app.main
starts. Only provider HTTP responses are synthetic. The input PNG and the
transport's MP4 are constructed before startup; the MP4 is outside the project
so native disk recovery cannot substitute it for a download. No executor,
FSM, provider method or application verifier is replaced. No managed grant.

Native observe mode has no durable MediaJob or artifact_verified event for
videos. The worker's original post-step file/Artifact validation remains
enabled; acceptance additionally calls the original structural verifier on
the actual downloaded output, without recording a fabricated business fact.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


PROJECT_ID = 2
NODE_KEY = "native-video-A"
OUTSIDE_KEY = "outside-native-video-assemble-Z"
ORIGIN = "http://127.0.0.1:18766"
BUCKET = "native-video-bucket"
MODEL = "bytedance/seedance-1.5-pro"
JOB_ID = "fixture-native-video-01"
OUTPUT_URL = ORIGIN + "/artifacts/" + JOB_ID + ".mp4"
FRAME_UUID = uuid.uuid5(uuid.NAMESPACE_URL, "synthetic/native-video/frame/1").hex
INPUT_UUID = uuid.uuid5(uuid.NAMESPACE_URL, "synthetic/native-video/image/1").hex
PROMPT = ("A synthetic geometric object turns slowly on a plain background. "
          "One continuous shot, steady camera, consistent lighting, no cuts.")
IDENTITY_FIELDS = ("id", "project_id", "uuid", "number", "sort_key", "voiceover_text",
                   "image_prompt", "animation_prompt")


def _write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read(path):
    connection = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _state(fixtures):
    replicas = {}
    for name, path in (("master", fixtures.DATA / "state.db"), ("project", fixtures.DB)):
        db = _read(path)
        try:
            project = dict(db.execute("SELECT * FROM projects WHERE id=?", (PROJECT_ID,)).fetchone())
            project["meta"] = json.loads(project["meta"] or "{}")
            frames = [dict(row) for row in db.execute(
                "SELECT * FROM frames WHERE project_id=? ORDER BY number", (PROJECT_ID,))]
            artifacts = [dict(row) for row in db.execute(
                "SELECT * FROM artifacts WHERE project_id=? ORDER BY id", (PROJECT_ID,))]
            hitl = [dict(row) for row in db.execute(
                "SELECT * FROM hitl_requests WHERE project_id=? ORDER BY id", (PROJECT_ID,))]
            nodes = {row["node_key"]: dict(row) for row in db.execute(
                "SELECT nr.* FROM node_runs nr JOIN workflow_runs wr ON nr.workflow_run_id=wr.id "
                "WHERE wr.project_id=? ORDER BY nr.node_key", (PROJECT_ID,))}
            for row in frames:
                row["attrs"] = json.loads(row["attrs"] or "{}")
            for row in artifacts:
                row["meta"] = json.loads(row["meta"] or "{}")
            for row in hitl:
                row["payload"] = json.loads(row["payload"] or "{}")
            replicas[name] = {"project": project, "frames": frames, "artifacts": artifacts,
                              "hitl": hitl, "nodes": nodes}
        finally:
            db.close()
    return replicas


def prepare(fixtures, root, mode):
    """Construct synthetic prerequisites only, on the copied seed before startup."""
    assert mode == "native-video", mode
    assert Path("/.dockerenv").is_file(), "parent's offline container only"
    assert "app.settings" not in sys.modules, "prepare before application imports/startup"
    root, project_dir = Path(root).resolve(), Path(fixtures.DB).parent.resolve()
    paths = (Path(fixtures.DATA) / "state.db", Path(fixtures.DB))
    for path in paths:
        assert path.resolve().is_relative_to(root) and path.is_file(), "reuse the copied group seed"
    baseline = _state(fixtures)
    for replica in baseline.values():
        assert not replica["frames"] and not replica["hitl"] and not replica["artifacts"], \
            "native-video requires a fresh seed without existing media or approvals"
        assert not replica["project"]["meta"].get("user_stop"), "retain any real stop; do not clear it"
    frame_id = 1
    for path in paths:
        db = _read(path)
        try:
            assert db.execute("SELECT count(*) FROM workflow_runs WHERE project_id=?",
                              (PROJECT_ID,)).fetchone()[0] == 1
            frame_id = max(frame_id, (db.execute("SELECT max(id) FROM frames").fetchone()[0] or 0) + 1)
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in ("action_execution_controls", "managed_run_allowances", "orchestrator_media_jobs"):
                if table in tables:
                    assert not db.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchone(), table
        finally:
            db.close()
    assert not list((project_dir / "videos").glob("*.mp4")), "no recoverable clip input"
    assert not list((project_dir / "scenes").glob("*")), "no unrelated image or shot2 input"
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    assert ffmpeg and ffprobe, "diagnostic FFmpeg and ffprobe are required; no substituted verifier"
    from PIL import Image

    scenes = project_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    image_path = scenes / "frame_001_native_video_input.png"
    image = Image.frombytes("RGB", (360, 640), random.Random(71).randbytes(360 * 640 * 3))
    image.save(image_path, "PNG")
    assert 200_000 < image_path.stat().st_size < 1_800_000, "native image threshold; retain original PNG upload"
    transport_dir = root / "native-video-transport"
    transport_dir.mkdir()
    video_path = transport_dir / "provider-output.mp4"
    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
               "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=12:duration=5",
               "-an", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
               "-threads", "1", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video_path)]
    subprocess.run(command, check=True, capture_output=True, timeout=45)
    assert video_path.stat().st_size > 1000 and not video_path.is_relative_to(project_dir)
    nodes = [
        {"id": NODE_KEY, "type": "videos", "position": {"x": 100, "y": 100},
         "data": {"label": "Synthetic native video", "modelId": "seedance-1-5-pro", "aspectRatio": "9:16"}},
        {"id": OUTSIDE_KEY, "type": "assemble", "position": {"x": 500, "y": 100},
         "data": {"label": "Outside video observation"}},
    ]
    edges = [{"id": "native-video-after", "source": NODE_KEY, "target": OUTSIDE_KEY,
              "data": {"kind": "after"}}]
    for path in paths:
        with sqlite3.connect(path) as db:
            meta = baseline["master" if path == paths[0] else "project"]["project"]["meta"]
            meta.setdefault("canvas_graph", {}).update(nodes=nodes, edges=edges)
            meta.update(img_streams=1, outsee_streams=1)
            db.execute("UPDATE projects SET status='new',auto_mode=0,video_generator='seedance_1_5_pro',"
                       "video_resolution='720p',meta=? WHERE id=?", (json.dumps(meta), PROJECT_ID))
            db.execute("INSERT INTO frames(id,project_id,number,voiceover_text,image_prompt,animation_prompt,"
                       "status,attrs,uuid,sort_key,created_at,updated_at) "
                       "VALUES(?,?,1,?,?,?,'planned',?,?,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                       (frame_id, PROJECT_ID, "Synthetic video input voiceover.",
                        "Synthetic geometric object, plain background.", PROMPT,
                        json.dumps({"fixture_input": "native-video"}), FRAME_UUID))
            db.execute("INSERT INTO artifacts(project_id,frame_id,kind,uuid,path,meta,created_at) "
                       "VALUES(?,?,'scene_image',?,?,?,CURRENT_TIMESTAMP)",
                       (PROJECT_ID, frame_id, INPUT_UUID, str(image_path),
                        json.dumps({"shot": 1, "fixture_input": "native-video"})))
            run_id = db.execute("SELECT id FROM workflow_runs WHERE project_id=?", (PROJECT_ID,)).fetchone()[0]
            db.execute("UPDATE workflow_runs SET nodes_snapshot=?,edges_snapshot=? WHERE id=?",
                       (json.dumps(nodes), json.dumps(edges), run_id))
            db.execute("DELETE FROM node_runs WHERE workflow_run_id=?", (run_id,))
            for node in nodes:
                db.execute("INSERT INTO node_runs(workflow_run_id,node_key,node_type,status,progress,attempts,"
                           "meta,created_at,updated_at) VALUES(?,?,?,'pending',0,0,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                           (run_id, node["id"], node["type"]))
    config = {"version": 1, "mode": "synthetic", "online_enabled": False, "endpoint": None,
              "decision": None, "profiles": {}, "allowed_actions": []}
    config_path = root / "synthetic-native-video-runtime.json"
    _write(config_path, config)
    # These are dummy diagnostic credentials for original native HTTP clients.
    # Outsee's key selects its existing no-CDP branch; the actual model is KIE.
    os.environ.update({"ORCHESTRATOR_RUNTIME_CONFIG": str(config_path), "VIDEO_PROVIDER": "kie",
        "KIE_API_KEY": "synthetic-native-video-token", "KIE_API_BASE_URL": ORIGIN,
        "OUTSEE_API_KEY": "synthetic-native-video-no-cdp", "OUTSEE_API_BASE_URL": ORIGIN,
        "VPS_RELAY_BASE_URL": "", "GPT_RELAY_TOKEN": "",
        "YANDEX_STORAGE_BUCKET": BUCKET, "YANDEX_STORAGE_ENDPOINT": ORIGIN,
        "YANDEX_STORAGE_ACCESS_KEY": "synthetic-video-access", "YANDEX_STORAGE_SECRET_KEY": "synthetic-video-secret"})
    fixtures.NODES = (NODE_KEY, OUTSIDE_KEY)
    prepared = {"scenario": mode, "node_key": NODE_KEY, "outside": OUTSIDE_KEY,
        "scope": [NODE_KEY], "frame_id": frame_id, "frame_uuid": FRAME_UUID, "input_artifact_uuid": INPUT_UUID,
        "input_image": str(image_path), "input_sha256": _sha(image_path),
        "transport_video": str(video_path), "transport_sha256": _sha(video_path),
        "config_sha256": _sha(config_path), "ffmpeg_command": command,
        "initial_state": _state(fixtures), "managed_eligible": False,
        "interventions": ["fresh copied group seed; one explicit Frame and scene_image input before startup",
            "locally generated transport MP4 outside project; no existing scene_video output",
            "all NodeRuns pending, auto_mode=False; original video API starts the worker",
            "original S3 image PUT/GET and KIE submit/poll/download use configured loopback endpoints",
            "synthetic no-CDP credential; explicit original Seedance model; native retry ladder unchanged",
            "one known provider job, first poll running then immutable success; reject extra submits",
            "no managed grant, profile change, HITL approval, force wipe or input edit after start"]}
    fixtures.native_video_prepared = prepared
    _write(root / "native-video-prepared.json", prepared)
    return prepared


def handler_for(fixtures, root, *, base_handler=None):
    """Serve only the original native client's external HTTP contracts."""
    root, prepared = Path(root), fixtures.native_video_prepared
    image, video = Path(prepared["input_image"]).read_bytes(), Path(prepared["transport_video"]).read_bytes()

    class Handler(base_handler or fixtures.FixtureHandler):
        video_counts = Counter()
        video_requests = []
        video_violations = []
        uploaded_path = None
        lock = threading.Lock()

        def reply(self, body, content_type="application/json"):
            content = json.dumps(body).encode() if isinstance(body, dict) else body
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def rejected(self, error):
            type(self).video_violations.append({"method": self.command, "path": self.path,
                                               "error_type": type(error).__name__})
            _write(root / "fixture-native-video-violations.json", type(self).video_violations)
            self.send_error(422, "Synthetic native video transport contract failed")

        def do_PUT(self):
            try:
                with self.lock:
                    assert self.path.startswith("/" + BUCKET + "/vp-frames/") and self.path.endswith(".png")
                    body = self.rfile.read(int(self.headers["Content-Length"]))
                    assert body == image and self.headers["x-amz-content-sha256"] == prepared["input_sha256"]
                    assert self.headers["Authorization"].startswith("AWS4-HMAC-SHA256 Credential=synthetic-video-access/")
                    self.video_counts["image_put"] += 1
                    assert self.video_counts["image_put"] == 1, "no repeated input upload"
                    type(self).uploaded_path = self.path
                    self.video_requests.append({"method": "PUT", "path": self.path,
                        "input_sha256": prepared["input_sha256"], "frame_id": prepared["frame_id"], "frame_uuid": FRAME_UUID})
                    _write(root / "fixture-native-video-requests.json", self.video_requests)
                self.reply(b"", "application/octet-stream")
            except Exception as error:
                self.rejected(error)

        def do_POST(self):
            try:
                with self.lock:
                    assert self.path == "/api/v1/jobs/createTask"
                    assert self.headers.get("x-diagnostic-node") == NODE_KEY, "original observed worker required"
                    body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    assert body["model"] == MODEL and self.uploaded_path
                    inputs = body["input"]
                    assert inputs["image_urls"] == [ORIGIN + self.uploaded_path]
                    assert PROMPT in inputs["prompt"] and inputs["generate_audio"] is False
                    assert inputs["duration"] == 5 and inputs["resolution"] == "720p" and inputs["aspect_ratio"] == "9:16"
                    self.video_counts["generation_submit"] += 1
                    assert self.video_counts["generation_submit"] == 1, "known job must not cause a second submit"
                    self.video_requests.append({"method": "POST", "path": self.path,
                        "node_key": NODE_KEY, "provider_job_id": JOB_ID, "body": body})
                    _write(root / "fixture-native-video-requests.json", self.video_requests)
                self.reply({"code": 200, "data": {"taskId": JOB_ID}})
            except Exception as error:
                self.rejected(error)

        def do_GET(self):
            try:
                with self.lock:
                    parsed = urlsplit(self.path)
                    if parsed.path == self.uploaded_path:
                        self.video_counts["image_verify"] += 1
                        reply, content_type = image, "image/png"
                    elif parsed.path == "/api/v1/jobs/recordInfo":
                        assert parse_qs(parsed.query) == {"taskId": [JOB_ID]}
                        assert self.video_counts["generation_submit"] == 1
                        self.video_counts["status"] += 1
                        assert self.video_counts["status"] <= 4, "bounded polling"
                        state = "running" if self.video_counts["status"] == 1 else "success"
                        reply = {"code": 200, "data": {"taskId": JOB_ID, "state": state}}
                        if state == "success":
                            reply["data"]["resultJson"] = json.dumps({"resultUrls": [OUTPUT_URL]})
                        content_type = "application/json"
                        self.video_requests.append({"method": "GET", "path": self.path,
                            "provider_job_id": JOB_ID, "state": state})
                    else:
                        assert parsed.path == "/artifacts/" + JOB_ID + ".mp4" and not parsed.query
                        assert self.video_counts["status"] >= 2
                        self.video_counts["download"] += 1
                        reply, content_type = video, "video/mp4"
                        self.video_requests.append({"method": "GET", "path": self.path,
                            "provider_job_id": JOB_ID, "output_sha256": prepared["transport_sha256"]})
                    _write(root / "fixture-native-video-requests.json", self.video_requests)
                self.reply(reply, content_type)
            except Exception as error:
                self.rejected(error)

    fixtures.native_video_handler = Handler
    return Handler


def _assert_boundary(state, prepared):
    for name, replica in state.items():
        assert not replica["project"]["auto_mode"]
        outside = replica["nodes"][OUTSIDE_KEY]
        assert (outside["status"], outside["attempts"]) == ("pending", 0), outside
        assert len(replica["frames"]) == 1, "no extra Frame created after start"
        frame = replica["frames"][0]
        source = prepared["initial_state"][name]["frames"][0]
        assert {key: frame[key] for key in IDENTITY_FIELDS} == {key: source[key] for key in IDENTITY_FIELDS}
        for field in ("general_plan", "script_text"):
            assert replica["project"][field] == prepared["initial_state"][name]["project"][field]
        image_inputs = [row for row in replica["artifacts"] if row["kind"] == "scene_image"]
        assert image_inputs == prepared["initial_state"][name]["artifacts"], "retain source Artifact identity"
        assert not [row for row in replica["artifacts"] if row["kind"] not in {"scene_image", "scene_video"}]
    assert _sha(prepared["input_image"]) == prepared["input_sha256"]
    assert _sha(prepared["transport_video"]) == prepared["transport_sha256"]


async def _history(client, prefix):
    events, after = [], 0
    while True:
        response = await client.get(prefix + "/events", params={"after": after, "limit": 100})
        response.raise_for_status()
        page = response.json()["events"]
        if not page:
            return events
        events.extend(page)
        assert page[-1]["id"] > after
        after = page[-1]["id"]


async def run(client, fixtures, observer, root, result, prepared, *, browser=False, timeout_s=120):
    from app.services.artifact_verifier import verify_artifact
    from app.services.node_capabilities import capability_for
    from app.services.step_cancel import is_generation_active
    from app.settings import settings

    assert not browser and 0 < timeout_s <= 120
    assert capability_for("videos").managed is False, "no capability grant for this observation probe"
    assert settings.kie_api_base_url == settings.yandex_storage_endpoint == ORIGIN
    assert settings.kie_api_key == "synthetic-native-video-token"
    root = Path(root)
    handler = fixtures.native_video_handler
    initial = _state(fixtures)
    _assert_boundary(initial, prepared)
    for replica in initial.values():
        assert replica["project"]["status"] == "new" and not replica["hitl"]
        assert all(node["status"] == "pending" and node["attempts"] == 0 for node in replica["nodes"].values())
        assert [row["kind"] for row in replica["artifacts"]] == ["scene_image"]
    base = "http://127.0.0.1:18765/api/projects/2"
    response = await client.post(base + "/observations", json={"node_keys": [NODE_KEY]})
    response.raise_for_status()
    scope = response.json()
    assert scope["mode"] == "observe" and scope["node_keys"] == [NODE_KEY]
    prefix = base + "/observations/" + scope["execution_id"]
    result.update(execution_id=scope["execution_id"], observation_only=True, managed_ready=False,
                  operator_starts=0, interventions=prepared["interventions"], semantic_quality_verified=False)
    response = await client.post(base + "/steps/video/run", params={"node_key": NODE_KEY, "force_wipe": "false"})
    response.raise_for_status()
    result["operator_starts"] = 1
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        await observer.catch_up()
        state = _state(fixtures)
        _assert_boundary(state, prepared)
        assert not handler.video_violations, handler.video_violations
        assert state["project"]["nodes"][NODE_KEY]["status"] != "failed", "native video worker failed"
        if (state["project"]["nodes"][NODE_KEY]["status"] == "done"
                and state["project"]["project"]["status"] == "videos_ready" and not is_generation_active(PROJECT_ID)):
            break
        await asyncio.sleep(0.25)
    else:
        raise AssertionError("native video did not reach its original videos_ready/HITL boundary within timeout")
    await observer.catch_up()
    history = await _history(client, prefix)
    starts = [event for event in history if event["node_key"] == NODE_KEY and event["kind"] == "node_start"]
    ends = [event for event in history if event["node_key"] == NODE_KEY and event["kind"] == "node_end"]
    assert len(starts) == len(ends) == 1 and starts[0]["attempt_id"] == ends[0]["attempt_id"]
    assert not any(event["kind"] in {"node_error", "model_error"} or event["node_key"] == OUTSIDE_KEY for event in history)
    counts = dict(handler.video_counts)
    assert counts == {"image_put": 1, "image_verify": 1, "generation_submit": 1, "status": 2, "download": 1}, counts
    submits = [request for request in handler.video_requests if request["method"] == "POST"]
    polls = [request for request in handler.video_requests if "state" in request]
    assert len(submits) == 1 and [request["state"] for request in polls] == ["running", "success"]
    assert {request["provider_job_id"] for request in polls} == {JOB_ID}
    actual_requests = json.loads((root / "actual-worker-requests.json").read_text(encoding="utf-8"))
    assert len(actual_requests) == 1
    assert actual_requests[0]["node_key"] == NODE_KEY and actual_requests[0]["attempt_id"] == starts[0]["attempt_id"]
    assert actual_requests[0]["body"] == submits[0]["body"]
    native = state["project"]
    assert native["nodes"][NODE_KEY]["attempts"] == 1, "one original native attempt"
    frame = native["frames"][0]
    assert frame["status"] == "video_generated" and frame["attrs"] == prepared["initial_state"]["project"]["frames"][0]["attrs"]
    outputs = [row for row in native["artifacts"] if row["kind"] == "scene_video"]
    assert len(outputs) == 1
    artifact = outputs[0]
    assert artifact["project_id"] == PROJECT_ID and artifact["frame_id"] == prepared["frame_id"] == frame["id"]
    assert frame["uuid"] == FRAME_UUID and artifact["uuid"] != INPUT_UUID and len(artifact["uuid"]) == 32
    assert artifact["meta"] == {"shot": 1} and artifact["approved_at"] is None
    output = Path(artifact["path"]).resolve()
    assert output.parent == fixtures.DB.parent.resolve() / "videos" and output.name.startswith("clip_001_")
    assert output.suffix == ".mp4" and "_s2_" not in output.name
    assert _sha(output) == prepared["transport_sha256"]
    verification = await verify_artifact(output, root=fixtures.DB.parent, kind="video",
        expected={"width": 720, "height": 1280, "min_duration": 4.9, "max_duration": 5.1})
    assert verification["status"] == "passed" and verification["sha256"] == prepared["transport_sha256"], verification
    assert verification["streams"] == ["video"] and verification["semantic_quality_verified"] is False
    sidecar = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    assert sidecar["raw_url"] == OUTPUT_URL and sidecar["provider"] == "kie" and sidecar["model"] == "seedance-1-5-pro"
    assert sidecar["path"] == str(output) and sidecar["bytes"] == output.stat().st_size
    assert PROMPT in sidecar["prompt"] and sidecar["params"]["project_id"] == PROJECT_ID
    assert len(native["hitl"]) == 1
    approval = native["hitl"][0]
    assert approval["project_id"] == PROJECT_ID and approval["frame_id"] is None
    assert approval["kind"] == "approve_videos" and approval["decision"] == "pending" and approval["decided_at"] is None
    assert approval["payload"]["step"] == "videos" and approval["payload"]["count"] == 1
    response = await client.get(prefix + "/context")
    response.raise_for_status()
    context = response.json()
    assert context["project"]["project_status"] == "videos_ready" and context["project"]["nodes"][NODE_KEY]["status"] == "done"
    response = await client.get(prefix + "/actions")
    response.raise_for_status()
    controls = response.json()
    assert not controls["configured"] and not controls["enabled"] and not controls["records"], controls
    after = _state(fixtures)
    _assert_boundary(after, prepared)
    assert after["project"]["artifacts"] == native["artifacts"] and after["project"]["hitl"] == native["hitl"]
    assert dict(handler.video_counts) == counts and not handler.video_violations
    _write(root / "native-video-history.json", history)
    _write(root / "native-video-state.json", after)
    result.update(accepted=True, acceptance_level="synthetic original video worker observation only",
        native_video_execution_observed=True, native_video_fixture_counts=counts,
        native_video_output={"artifact": artifact, "frame_identity": {key: frame[key] for key in IDENTITY_FIELDS},
            "provider_job_id": JOB_ID, "source_image_artifact_uuid": INPUT_UUID,
            "source_image_sha256": prepared["input_sha256"], "verification": verification},
        native_video_pending_hitl_id=approval["id"], structural_verification_source="original artifact_verifier; acceptance read only",
        native_worker_validation="original post_step_validate.validate_after_videos",
        durable_native_media_job_verified=False, managed_ready=False, automatic_continuation_verified=False)
    _write(root / "native-video-result.json", result)
