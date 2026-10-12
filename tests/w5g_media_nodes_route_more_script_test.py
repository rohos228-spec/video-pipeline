"""Guards for scripts/e2e_media_production_route_more.py (nodes hero, items, videos): pure helpers, the request Gate, the
paid-body refusals, the fake Outsee (image and video), the checks, and a real --dry run per node.

Nothing here sends anything outside this machine and nothing here runs --live: the live-only paths are covered by the
request Gate (a pure object that judges every outgoing request), by the paid-body check and by the argument / env-file
refusals that return before the application starts. The dry runs start the whole application in a SUBPROCESS (isolated
memory and ports), so run this file alone: one pytest at a time.
"""
import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_PATH = ROOT / "scripts" / "e2e_media_production_route_more.py"
_spec = importlib.util.spec_from_file_location("e2e_media_production_route_more", _PATH)
script = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = script
_spec.loader.exec_module(script)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stand_workdir import make_workdir, remove_workdir  # noqa: E402

MAIN, FAKE = 18798, 18799
NODES = ("hero", "items", "videos")
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


@pytest.fixture
def tmp_path():
    """Outside the repository: the e2e guard refuses a work folder inside live data."""
    path = make_workdir()
    try:
        yield path
    finally:
        remove_workdir(path)


# ---------- arguments and authorisation ----------

def test_exactly_one_of_dry_and_live_is_required_and_the_default_node_is_images():
    for bad in (["--work-dir", "w"], ["--work-dir", "w", "--dry", "--live", "--env-file", "e"]):
        with pytest.raises(SystemExit):
            script._args(bad)
    assert script._args(["--work-dir", "w", "--dry"]).node == "images"
    assert script._args(["--work-dir", "w", "--dry", "--node", "videos"]).node == "videos"
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--dry", "--node", "music"])  # music (Suno/Kie) is not provable here


def test_defaults_are_one_real_post_and_thirty_cents_per_node():
    for node in NODES:
        args = script._args(["--work-dir", "w", "--dry", "--node", node])
        assert (args.max_real_posts, args.max_usd) == (1, 0.30)
    assert script.DEFAULT_MAX_USD == 0.30


def test_the_conservative_price_per_node_fits_one_dollar_in_total_and_each_fits_its_authorisation():
    assert {n: script.PROFILES[n].price_usd for n in NODES} == {"hero": 0.10, "items": 0.10, "videos": 0.30}
    assert sum(script.PROFILES[n].price_usd for n in NODES) <= 1.0
    for node in NODES:
        assert script.authorisation_problem(node, 1, 0.30) is None
    assert "exceeds" in script.authorisation_problem("videos", 1, 0.25)
    assert "exceeds" in script.authorisation_problem("hero", 2, 0.15)
    assert script.authorisation_problem("hero", 0, 1) is not None and script.authorisation_problem("hero", 1, 0) is not None
    assert script.authorisation_problem("music", 1, 1) is not None
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--dry", "--node", "videos", "--max-usd", "0.20"])
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--live", "--env-file", "e", "--node", "hero", "--max-real-posts", "2", "--max-usd", "1"])


def test_live_needs_an_env_file_and_real_decision_is_a_live_option():
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--live", "--node", "hero"])
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--dry", "--node", "hero", "--real-decision"])
    assert script._args(["--work-dir", "w", "--live", "--env-file", "e", "--node", "items", "--real-decision"]).real_decision


def test_a_kie_image_generator_cannot_be_chosen():
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--dry", "--node", "hero", "--image-generator", "flux_2_pro"])
    assert script._args(["--work-dir", "w", "--dry", "--node", "hero"]).image_generator == "gpt_image_2_vip"


def test_the_application_caps_admit_exactly_one_submit_of_the_node():
    image_cap = script.gate_cap_usd("hero", 0.30)
    assert 0.30 <= image_cap < 0.60
    video_cap = script.gate_cap_usd("videos", 1.00)
    assert 1.00 <= video_cap < 1.30  # one video reservation passes; a second (1.00 + one recorded 0.30 row) does not


def test_live_refuses_before_starting_anything_without_a_key_or_with_a_bad_base(tmp_path, capsys):
    no_key = tmp_path / "nokey.env"
    no_key.write_text("KIE_API_KEY=x\n", encoding="utf-8")
    bad_base = tmp_path / "badbase.env"
    bad_base.write_text("OUTSEE_API_KEY=abcdefgh\nOUTSEE_API_BASE_URL=http://example.org\n", encoding="utf-8")
    for node in NODES:
        assert asyncio.run(script.main(["--node", node, "--work-dir", str(tmp_path / "w"), "--live",
                                        "--env-file", str(no_key)])) == 3
        assert asyncio.run(script.main(["--node", node, "--work-dir", str(tmp_path / "w"), "--live",
                                        "--env-file", str(bad_base)])) == 3
    out = capsys.readouterr().out
    assert "nothing sent" in out and "abcdefgh" not in out
    assert not (tmp_path / "w").exists()  # nothing was created: the refusal came first


def test_the_stand_refuses_to_start_when_the_application_settings_are_already_loaded(tmp_path, capsys):
    # this very process has imported the application: a stand started here would point at the LIVE data folder
    import app.settings  # noqa: F401
    assert asyncio.run(script.main(["--node", "hero", "--work-dir", str(tmp_path / "w"), "--dry"])) == 3
    assert "nothing was started" in capsys.readouterr().out
    assert not (tmp_path / "w").exists()


# ---------- the node profiles ----------

def test_profiles_match_the_registry_the_key_families_and_the_recovery_admission():
    from app.orchestrator.node_registry import WORK_NODES
    from app.services import media_local_resume, media_recovery
    for node in NODES:
        prof = script.PROFILES[node]
        assert WORK_NODES[node].step_code == prof.step_code
        assert media_recovery.KEY_FAMILY[node].match(prof.key)
        assert prof.paid_path in script.PAID_PATHS
    assert media_local_resume.admitted("hero") and media_local_resume.admitted("items")
    assert not media_local_resume.admitted("videos")  # the stand states this gap instead of pretending a resume exists
    assert script.PROFILES["videos"].paid_path == script.VIDEOS_PATH and script.PROFILES["hero"].paid_path == script.IMAGES_PATH


def test_the_cheapest_wired_models_are_the_chosen_ones():
    from app.bots import outsee_http
    assert script.VIDEO_MODEL in outsee_http.OUTSEE_WIRED_VIDEO_MODELS and len(outsee_http.OUTSEE_WIRED_VIDEO_MODELS) == 1
    assert set(script.IMAGE_MODELS) == set(outsee_http.OUTSEE_WIRED_IMAGE_MODELS)
    from app.generation_options import IMAGE_GENERATORS_BY_ID
    for gid in script.IMAGE_GENERATORS:
        assert outsee_http.studio_id_to_outsee_image_slug(IMAGE_GENERATORS_BY_ID[gid].outsee_slug) in script.IMAGE_MODELS
    assert script.VIDEO_SECONDS == 4 and script.VIDEO_RESOLUTION == "720p"  # shortest allowed duration, 720p


# ---------- the paid body: the stand's own authorisation ----------

def _image_body(**over):
    return {"prompt": "p", "model": "gpt-image-2", "aspect_ratio": "16:9", "resolution": "2K", **over}


def _video_body(**over):
    return {"prompt": "p", "model": "veo-3-1-lite", "aspect_ratio": "9:16", "resolution": "720p", "duration_sec": 4,
            "generate_audio": False, **over}


def test_a_clean_image_and_a_clean_video_body_pass():
    assert script.body_problem(script.IMAGES_PATH, _image_body()) is None
    assert script.body_problem(script.IMAGES_PATH, _image_body(model="nano-banana-2")) is None
    assert script.body_problem(script.VIDEOS_PATH, _video_body()) is None


@pytest.mark.parametrize("path,body", [
    (script.IMAGES_PATH, _image_body(model="seedance-2-5")),
    (script.IMAGES_PATH, _image_body(model="nano-banana-pro")),
    (script.IMAGES_PATH, _image_body(model="flux-2-pro")),
    (script.VIDEOS_PATH, _video_body(model="seedance-2-5")),
    (script.VIDEOS_PATH, _video_body(model="kling-2-6")),
    (script.VIDEOS_PATH, _video_body(resolution="1080p")),
    (script.VIDEOS_PATH, _video_body(duration_sec=8)),
    (script.VIDEOS_PATH, _video_body(duration_sec=None)),
    (script.VIDEOS_PATH, _video_body(generate_audio=True)),
    (script.VIDEOS_PATH, _video_body(image_url="https://example.org/a.png")),
    (script.VIDEOS_PATH, _video_body(image_urls=["https://example.org/a.png", "https://example.org/b.png"])),
    ("/api/v1/audio/generate", {"model": "x"})])
def test_a_forbidden_or_dearer_body_is_refused_before_it_leaves(path, body):
    assert script.body_problem(path, body)


# ---------- the request Gate ----------

def _gate(mode="dry", node="hero", **kw):
    prof = script.PROFILES[node]
    if mode == "live":
        return script.MediaGate(mode="live", fake_port=FAKE, outsee_host="outsee.io", phase="main", paid_path=prof.paid_path, **kw)
    return script.MediaGate(mode="dry", fake_port=FAKE, paid_path=prof.paid_path, **kw)


@pytest.fixture
def paid_path_of(request):
    saved = script.base.GENERATE_PATH
    yield lambda node: setattr(script.base, "GENERATE_PATH", script.PROFILES[node].paid_path)
    script.base.GENERATE_PATH = saved


def test_a_video_node_pays_on_the_video_path_only_and_an_image_node_on_the_image_path_only(paid_path_of):
    paid_path_of("videos")
    video = _gate(node="videos", phase="main", max_paid_posts=1)
    assert video.judge("POST", "127.0.0.1", FAKE, script.IMAGES_PATH, is_decision=False).action == "refuse"
    assert video.judge("POST", "127.0.0.1", FAKE, script.VIDEOS_PATH, is_decision=False).kind == "paid_post_fake"
    assert video.judge("POST", "127.0.0.1", FAKE, script.VIDEOS_PATH, is_decision=False).action == "refuse"
    assert video.paid_posts == 1 and video.refused_cap == 1
    paid_path_of("hero")
    image = _gate(node="hero", phase="main", max_paid_posts=1)
    assert image.judge("POST", "127.0.0.1", FAKE, script.VIDEOS_PATH, is_decision=False).action == "refuse"
    assert image.judge("POST", "127.0.0.1", FAKE, script.IMAGES_PATH, is_decision=False).kind == "paid_post_fake"


def test_a_submit_of_either_kind_during_a_guard_probe_is_a_failure_and_never_a_paid_post(paid_path_of):
    paid_path_of("videos")
    gate = _gate(node="videos", phase="probe")
    assert gate.judge("POST", "127.0.0.1", FAKE, script.VIDEOS_PATH, is_decision=False).kind == "fake_submit_during_probe"
    assert gate.judge("POST", "127.0.0.1", FAKE, script.IMAGES_PATH, is_decision=False).kind == "fake_submit_during_probe"
    assert gate.probe_posts == 2 and gate.paid_posts == 0


def test_live_allows_one_paid_submit_of_the_nodes_own_kind_to_the_outsee_host_and_nothing_else_paid(paid_path_of):
    paid_path_of("videos")
    gate = _gate("live", "videos", max_paid_posts=1)
    assert gate.judge("POST", "outsee.io", 443, script.IMAGES_PATH, is_decision=False).action == "refuse"
    assert gate.judge("POST", "outsee.io", 443, script.VIDEOS_PATH, is_decision=False).kind == "paid_post_real"
    assert gate.judge("POST", "outsee.io", 443, script.VIDEOS_PATH, is_decision=False).action == "refuse"
    assert gate.paid_posts == 1
    for path in ("/api/v1/models", "/api/v1/audio/generate", "/api/v1/videos/generate/extra", "/"):
        assert gate.judge("POST", "outsee.io", 443, path, is_decision=False).action == "refuse", path
    assert gate.judge("GET", "outsee.io", 443, "/api/v1/generations/42", is_decision=False).kind == "outsee_get"
    assert gate.judge("GET", "example.org", 443, "/", is_decision=False).action == "refuse"


# ---------- the fake Outsee ----------

def _req(url, *, key=script.FAKE_KEY, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")
def test_the_fake_outsee_plays_image_and_video_submit_status_and_download_and_counts(tmp_path):
    fake = script.FakeOutseeMedia(FAKE).start()
    try:
        assert _req(fake.base_url + script.IMAGES_PATH, key=None, body={"prompt": "x"})[0] == 401
        assert fake.auth_failures == 1 and fake.posts == 0
        img = json.loads(_req(fake.base_url + script.IMAGES_PATH, body={"prompt": "p", "model": "gpt-image-2"})[1])
        vid = json.loads(_req(fake.base_url + script.VIDEOS_PATH, body={
            "prompt": "p", "model": "veo-3-1-lite", "resolution": "720p", "duration_sec": 4, "generate_audio": False})[1])
        assert img["id"] == "fake-0001" and vid["id"] == "fakevid-0001"
        assert (fake.posts_images, fake.posts_videos, fake.posts) == (1, 1, 2)
        assert fake.bodies[1]["path"] == script.VIDEOS_PATH and fake.bodies[1]["has_image"] is False
        img_done = json.loads(_req(fake.base_url + script.STATUS_PREFIX + img["id"])[1])
        vid_done = json.loads(_req(fake.base_url + script.STATUS_PREFIX + vid["id"])[1])
        assert img_done["result_urls"][0].endswith(".png") and vid_done["result_urls"][0].endswith(".mp4")
        status, png = _req(img_done["result_urls"][0], key=None)
        assert status == 200 and png[:8] == b"\x89PNG\r\n\x1a\n"
        status, mp4 = _req(vid_done["result_urls"][0], key=None)
        assert status == 200 and mp4[4:8] == b"ftyp"
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(mp4)
        from app.services.artifact_verifier import verify_artifact
        got = asyncio.run(verify_artifact(clip, root=tmp_path, kind="video"))
        assert got["status"] == "passed" and got["height"] == 720 and 7.5 <= got["duration"] <= 8.5
        assert fake.counts()["downloads"] == 2 and _req(fake.base_url + "/nothing")[0] == 404
    finally:
        fake.stop()


def test_the_hero_prompt_stub_answers_differently_every_time_like_a_real_gpt():
    stub = script.StubGpt()
    a = asyncio.run(stub.ask_fresh("x"))
    b = asyncio.run(stub.ask_fresh("x"))
    assert a != b and len(a) > 100 and stub.calls == 2


# ---------- environment ----------

def test_environment_is_synthetic_loopback_with_the_fake_provider_and_both_providers_on_outsee(tmp_path):
    env = script.env_overrides(tmp_path, MAIN, fake_base="http://127.0.0.1:18799", gate_cap=0.45, dry=True)
    assert env["OUTSEE_API_BASE_URL"] == "http://127.0.0.1:18799" and env["OUTSEE_API_KEY"] == script.FAKE_KEY
    assert env["IMAGE_PROVIDER"] == "outsee" and env["VIDEO_PROVIDER"] == "outsee"
    assert env["OUTSEE_DEFAULT_VIDEO_MODEL"] == "veo-3-1-lite"
    assert env["KIE_API_KEY"] == "" and env["ELEVENLABS_API_KEY"] == ""
    assert not any(k in env for k in ("GPT_RELAY_TOKEN", "GPT_PROXY_URL"))


def test_env_file_is_read_only_by_the_whitelist(tmp_path):
    env_file = tmp_path / "e.env"
    env_file.write_text("OUTSEE_API_KEY=secret-outsee\nKIE_API_KEY=kie\nELEVENLABS_API_KEY=el\nVIBECODE_API_KEY=vk\n"
                        "SUNO_API_KEY=suno\n", encoding="utf-8")
    got = script.base.load_env_whitelist(str(env_file), script.OUTSEE_ENV_NAMES)
    assert set(got) == {"OUTSEE_API_KEY"} and got["OUTSEE_API_KEY"] == "secret-outsee"


# ---------- checks ----------

def _good_video_snapshot(**over):
    blocked = {"blocked": True, "posts_delta": 0}
    snap = {
        "counters": {"paid_posts": 1, "probe_posts": 0, "decision_posts": 0, "refused_cap": 0, "unexpected_model": 0,
                     "injected_validate": 9, "fake_posts": 1, "fake_auth_failures": 0, "refused_foreign": 0},
        "probes": {"kill_switch": {**blocked, "code": "kill_switch_active"}, "disk_guard": {**blocked, "code": "disk_guard_blocked"},
                   "spend_cap": {**blocked, "code": "spend_cap_daily"}, "second_submit_cap": {**blocked, "code": "spend_cap_daily"},
                   "kill_switch_recovery": {"refused": True, "tickets_after": 0},
                   "resume_only_other": {"refused": True, "posts_delta": 0}},
        "diagnoses": {"incidents": [{"node_key": "media-videos-A", "type": "media_failure", "media": True}]},
        "decisions": {"records": [{"status": "human_review", "model_steps": 2}]},
        "actions": {"recoveries": []},
        "ledger_rows": [{"api": "outsee", "key": "clip_001", "event": "intent", "status": "bound"}],
        "spend_rows": [{"provider": "outsee", "kind": "estimated", "usd": 0.0, "note": "video veo-3-1-lite ok cost_unknown=true"}],
        "clips_on_disk": [{"name": "clip_001_ab12cd34.mp4", "exists": True, "bytes": 90000}],
        "node_status": {"media-videos-A": "failed"}, "node_attempts": 9,
        "step_failure": {"abandoned_at": "t"}, "hitl_pending": [], "refused_hosts": [],
        "artifact_check": {"status": "passed", "duration": 4.0, "streams": ["video"], "height": 720, "width": 1280},
        "paid_bodies": [_video_body()], "worst_case_usd": 0.30, "max_usd": 0.30,
    }
    snap.update(over)
    return snap


def test_a_good_dry_video_snapshot_passes_every_check_and_a_live_one_adds_the_cap_checks():
    checks = script.evaluate("videos", _good_video_snapshot(), mode="dry")
    assert script.verdict(checks) == (True, [])
    assert len({c["name"] for c in checks}) == len(checks)
    snap = _good_video_snapshot()
    snap["counters"]["fake_posts"] = None
    live = script.evaluate("videos", snap, mode="live")
    assert script.verdict(live) == (True, []) and {"caps_not_exceeded", "real_submit_sent"} <= {c["name"] for c in live}


def _failed_video(**over):
    return set(script.verdict(script.evaluate("videos", _good_video_snapshot(**over), mode="dry"))[1])


def test_a_second_video_submit_or_a_lost_clip_fails():
    snap = _good_video_snapshot()
    snap["counters"]["paid_posts"] = 2
    assert {"exactly_one_paid_submit_at_most", "no_second_submit_after_the_loss"} <= set(
        script.verdict(script.evaluate("videos", snap, mode="dry"))[1])
    assert "clip_survived_the_loss_on_disk_exactly_once" in _failed_video(clips_on_disk=[])
    two = [{"name": "a", "exists": True}, {"name": "b", "exists": True}]
    assert "clip_survived_the_loss_on_disk_exactly_once" in _failed_video(clips_on_disk=two)


def test_a_supervisor_that_paid_or_started_the_node_fails():
    ticket = {"status": "verified", "verification_result": {"new_paid_requests": 1, "node_started": True}}
    assert "supervisor_made_zero_new_paid_requests_and_did_not_start_the_node" in _failed_video(actions={"recoveries": [ticket]})
    ok = {"status": "verified", "verification_result": {"new_paid_requests": 0, "node_started": False}}
    assert "supervisor_made_zero_new_paid_requests_and_did_not_start_the_node" not in _failed_video(actions={"recoveries": [ok]})


def test_a_clip_that_is_not_short_silent_and_720p_or_does_not_decode_fails():
    assert "clip_is_short_silent_and_not_above_720p" in _failed_video(
        artifact_check={"status": "passed", "duration": 8.0, "streams": ["video"], "height": 720})
    assert "clip_is_short_silent_and_not_above_720p" in _failed_video(
        artifact_check={"status": "passed", "duration": 4.0, "streams": ["video", "audio"], "height": 720})
    assert "clip_is_short_silent_and_not_above_720p" in _failed_video(
        artifact_check={"status": "passed", "duration": 4.0, "streams": ["video"], "height": 1080})
    assert "downloaded_file_passes_the_real_verifier" in _failed_video(artifact_check={"status": "failed", "reason": "ValueError"})


def test_a_dearer_or_forbidden_paid_body_or_a_blown_authorisation_fails():
    assert "paid_body_is_the_cheapest_allowed_and_clean" in _failed_video(paid_bodies=[_video_body(resolution="1080p")])
    assert "paid_body_is_the_cheapest_allowed_and_clean" in _failed_video(paid_bodies=[_video_body(), _video_body()])
    assert "worst_case_within_authorisation" in _failed_video(worst_case_usd=0.31)


def test_a_guard_that_did_not_block_and_a_probe_that_reached_a_provider_are_named():
    snap = _good_video_snapshot()
    snap["probes"]["kill_switch"] = {"blocked": False, "code": "", "posts_delta": 1}
    snap["probes"]["resume_only_other"] = {"refused": False, "posts_delta": 1}
    snap["counters"]["probe_posts"] = 1
    snap["refused_hosts"] = ["vibecode.moe"]
    failed = set(script.verdict(script.evaluate("videos", snap, mode="dry"))[1])
    assert {"kill_switch_stops_the_paid_seam", "other_request_under_resume_only_is_refused", "no_probe_reached_a_provider",
            "no_unexpected_model_or_foreign_request", "dry_nothing_left_the_machine"} <= failed


def test_the_image_nodes_reuse_the_sibling_checks_on_their_own_key():
    snap = {
        "counters": {"paid_posts": 1, "probe_posts": 0, "decision_posts": 0, "refused_cap": 0, "unexpected_model": 0,
                     "injected_hitl": 9, "injected_tail": 0, "fake_posts": 1, "fake_auth_failures": 0, "refused_foreign": 0},
        "probes": {}, "diagnoses": {"incidents": []}, "decisions": {"records": []}, "actions": {"recoveries": []},
        "idem_rows": [{"event": "intent", "key": "hero_1_v1"}, {"event": "result", "key": "hero_1_v1", "file_sha256": "abc"}],
        "ledger_rows": [], "spend_rows": [], "recorded_file": {"exists": True, "sha256": "abc"},
        "node_status": {"media-hero-A": "failed"}, "node_attempts": 9, "step_failure": {"abandoned_at": "t"},
        "decision_contexts": [], "hitl_pending": [], "refused_hosts": [], "artifact_check": {"status": "passed", "width": 512, "height": 512},
        "paid_bodies": [_image_body()], "worst_case_usd": 0.10, "max_usd": 0.30}
    names_failed = set(script.verdict(script.evaluate("hero", snap, mode="dry"))[1])
    # the record of the unit is found under ITS key (hero_1_v1) and the loss counter is the hero one
    assert "result_recorded_before_the_loss" not in names_failed and "outcome_loss_injected_and_native_retries_ran" not in names_failed
    assert "downloaded_file_passes_the_real_verifier" not in names_failed and "paid_body_is_the_cheapest_allowed_and_clean" not in names_failed
    assert script.base.TARGET == "media-images-A" and script.base.FRAME_KEY == "frame_001"  # the sibling's globals are restored


def test_the_verdict_never_claims_a_live_proof_for_a_dry_run():
    assert script.final_verdict("hero", "dry", True).startswith("DRY OK") and "NOT a live proof" in script.final_verdict("hero", "dry", True)
    assert script.final_verdict("hero", "live", True).startswith("PASS") and script.final_verdict("videos", "dry", False) == "NOT PASSED"


def test_reality_report_is_honest_per_node():
    dry = script.reality_report("videos", "dry", {"counters": {"paid_posts": 1}})
    text = json.dumps(dry)
    assert "fake Outsee" in text and "TEXT-TO-VIDEO" in text and "nothing for the supervisor to resume" in text
    assert not any("real Outsee submit" in x for x in dry["real"])
    live = script.reality_report("hero", "live", {"counters": {"paid_posts": 1, "decision_posts": 0}})
    assert any("1 real Outsee submit" in x for x in live["real"]) and "GPT stub" in json.dumps(live)
    assert any("NOT a live proof" in x for x in script.reality_not_proven("hero", "dry"))
    assert not any("NOT a live proof" in x for x in script.reality_not_proven("hero", "live"))
    assert any("not implemented" in x for x in script.reality_not_proven("videos", "live"))


# ---------- production defects found by this stand (regression guards) ----------

def test_every_managed_capability_has_a_resolvable_executor_description():
    """hero ('... generate_image_with_retries (Outsee / Kie)') and sfx_gen ('... elevenlabs_api/local_synth') were
    unresolvable, so the node_code part of the input revision held a random nonce: the revision was never equal to itself
    and every Decision context of those nodes was stale (human_review)."""
    from app.services import node_capabilities as nc
    from app.services.revisions import code_files, executor_modules
    caps = next(v for n in dir(nc) for v in [getattr(nc, n)] if isinstance(v, dict) and v
                and all(hasattr(x, "executor") for x in v.values()))
    unresolved = {}
    for node_type, cap in caps.items():
        if not cap.managed:
            continue
        try:
            executor_modules(cap)
        except LookupError as exc:
            unresolved[node_type] = str(exc)
    assert unresolved == {}
    files = executor_modules(caps["hero"])
    assert "app/orchestrator/steps/generate_hero.py" in files and "app/services/outsee_retry.py" in files
    assert "app/services/sfx_gen.py" in executor_modules(caps["sfx_gen"])
    assert code_files("hero", caps["hero"])  # the whole code part of the revision resolves


def test_a_node_code_part_of_the_revision_is_equal_to_itself_for_hero_and_sfx_gen():
    from app.services import node_capabilities as nc
    from app.services.revisions import code_files, _file_hash
    caps = next(v for n in dir(nc) for v in [getattr(nc, n)] if isinstance(v, dict) and v
                and all(hasattr(x, "executor") for x in v.values()))
    for node in ("hero", "sfx_gen"):
        first = {rel: _file_hash(rel) for rel in code_files(node, caps[node])}
        second = {rel: _file_hash(rel) for rel in code_files(node, caps[node])}
        assert first == second and first


def test_the_voice_inputs_of_assemble_are_hashed_by_content_not_by_mtime(tmp_path):
    """assemble rewrites audio/voice_full.mp3 on every attempt (the same bytes): that is not a changed input."""
    from app.services.revisions import _files_under
    audio = tmp_path / "audio"
    audio.mkdir()
    voice = audio / "voice_full.mp3"
    voice.write_bytes(b"ID3" + b"\x01" * 4000)
    (audio / "frame_001.mp3").write_bytes(b"frame")
    before = _files_under(audio, (".mp3",), prefix="voice", content=True)
    assert set(before) == {"voice_full.mp3"} and "sha256" in before["voice_full.mp3"]
    stat_before = _files_under(audio, (".mp3",), prefix="voice")
    os.utime(voice, (1, 1))
    voice.write_bytes(b"ID3" + b"\x01" * 4000)  # the same bytes written again, a new mtime
    assert _files_under(audio, (".mp3",), prefix="voice", content=True) == before
    assert _files_under(audio, (".mp3",), prefix="voice") != stat_before  # the old stat-based row WOULD have changed
    voice.write_bytes(b"ID3" + b"\x02" * 4000)
    assert _files_under(audio, (".mp3",), prefix="voice", content=True) != before


# ---------- a real dry run per node (whole app in a subprocess, nothing leaves the machine) ----------

def _run(tmp_path: Path, node: str, *extra: str) -> tuple[int, dict, str]:
    env = {**os.environ, "PYTHONUTF8": "1", "NO_COLOR": "1"}
    for name in ("GPT_RELAY_TOKEN", "GPT_PROXY_URL", "VIBECODE_API_KEY", "GPT_API_KEY", "OUTSEE_API_KEY", "KIE_API_KEY",
                 "ELEVENLABS_API_KEY"):
        env.pop(name, None)
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(_PATH), "--node", node, "--work-dir", str(tmp_path), "--dry", "--port", str(MAIN),
         "--fake-port", str(FAKE), "--settle-s", "3", "--sleep-minutes", "0.01", "--timeout-s", "420", *extra],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=560)
    result_path = tmp_path / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    return proc.returncode, result, (proc.stdout or "")[-1800:]


def _common_dry_asserts(result: dict, node: str) -> None:
    summary, counters = result["summary"], result["counters"]
    assert result["mode"] == "dry" and result["ok"] is True, result.get("failed")
    assert summary["verdict"].startswith("DRY OK") and "NOT a live proof" in summary["verdict"]
    assert counters["paid_posts"] == 1 and counters["fake_posts"] == 1 and counters["probe_posts"] == 0
    assert result["refused_hosts"] == [] and counters["unexpected_model"] == 0 and result["node_attempts"] >= 9
    assert result["node_status"][script.PROFILES[node].target] == "failed"  # the supervisor never starts the node
    assert result["artifact_check"]["status"] == "passed"
    assert len(result["paid_bodies"]) == 1 and script.body_problem(script.PROFILES[node].paid_path, result["paid_bodies"][0]) is None
    assert result["summary"]["caps"]["worst_case_usd"] <= result["summary"]["caps"]["max_usd"] == 0.30
    assert "fake" in json.dumps(result["reality"]["not_real"])


@pytest.mark.parametrize("node", ("hero", "items"))
def test_a_real_dry_run_proves_the_image_node_offline(tmp_path, node):
    code, result, tail = _run(tmp_path, node)
    assert code == 0, tail
    _common_dry_asserts(result, node)
    key = script.PROFILES[node].key
    assert result["counters"]["injected_hitl"] + result["counters"]["injected_tail"] >= 3
    ticket = result["actions"]["recoveries"][0]
    assert ticket["status"] == "verified" and ticket["preconditions"]["media_operation"] == "resume_local"
    assert ticket["verification_result"]["new_paid_requests"] == 0
    # ONE paid request, recorded once, never consumed, never asked again after the loss
    assert [e[0] for e in result["idem_events"] if e[1] == key] == ["intent", "result"]
    assert result["fake_after_probes"]["posts"] == 1
    if node == "hero":
        assert result["counters"]["gpt_stub_calls"] == 1  # the saved prompt is reused: GPT is not asked again
    assert all(result["probes"][k]["posts_delta"] == 0 for k in
               ("kill_switch", "disk_guard", "spend_cap", "second_submit_cap", "identical_request", "resume_only_other"))


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")
def test_a_real_dry_run_proves_the_video_node_offline(tmp_path):
    code, result, tail = _run(tmp_path, "videos")
    assert code == 0, tail
    _common_dry_asserts(result, "videos")
    assert result["counters"]["injected_validate"] >= 3
    assert result["paid_bodies"][0]["duration_sec"] == 4 and result["paid_bodies"][0]["resolution"] == "720p"
    assert result["paid_bodies"][0]["has_image"] is False
    assert len(result["clips_on_disk"]) == 1 and result["fake_after_probes"]["posts_videos"] == 1
    assert result["artifact_check"]["height"] == 720 and "audio" not in result["artifact_check"]["streams"]
    assert result["actions"]["recoveries"] == []  # nothing to resume: stated, not hidden
    assert any("no request-key record" in x for x in result["reality"]["not_real"])


@pytest.mark.slow
def test_the_dry_run_refuses_a_work_dir_inside_live_data():
    proc = subprocess.run([sys.executable, "-X", "utf8", str(_PATH), "--node", "hero", "--work-dir", str(ROOT / "data" / "x"),
                           "--dry"], cwd=ROOT, env={**os.environ, "PYTHONUTF8": "1"}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode != 0 and not (ROOT / "data" / "x" / "result.json").exists()


def test_a_portrait_720p_clip_passes_and_a_portrait_1080p_clip_fails():
    # 720p is the SHORT side: the real Outsee clip of the live run was 720x1280 (found on 2026-10-10)
    assert "clip_is_short_silent_and_not_above_720p" not in _failed_video(
        artifact_check={"status": "passed", "duration": 4.0, "streams": ["video"], "width": 720, "height": 1280})
    assert "clip_is_short_silent_and_not_above_720p" in _failed_video(
        artifact_check={"status": "passed", "duration": 4.0, "streams": ["video"], "width": 1080, "height": 1920})
