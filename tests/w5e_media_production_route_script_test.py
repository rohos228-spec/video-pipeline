"""Guards for scripts/e2e_media_production_route.py (pure helpers, the fake Outsee, and a real --dry run).

Nothing here sends anything outside this machine and nothing here runs --live: the live-only paths are covered by
the request Gate (a pure object that judges every outgoing request) and by the argument / env-file refusals that
return before the application starts. The dry run starts the whole application in a SUBPROCESS (isolated memory and
ports), so run this file alone: one pytest at a time.
"""
import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_PATH = ROOT / "scripts" / "e2e_media_production_route.py"
_spec = importlib.util.spec_from_file_location("e2e_media_production_route", _PATH)
script = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = script
_spec.loader.exec_module(script)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stand_workdir import make_workdir, remove_workdir  # noqa: E402

MAIN, FAKE = 18768, 18769


@pytest.fixture
def tmp_path():
    """Outside the repository: the e2e guard refuses a work folder inside live data."""
    path = make_workdir()
    try:
        yield path
    finally:
        remove_workdir(path)


# ---------- arguments and authorisation ----------

def test_exactly_one_of_dry_and_live_is_required():
    for bad in ([], ["--dry", "--live", "--env-file", "e"], ["--work-dir", "w"]):
        with pytest.raises(SystemExit):
            script._args(["--work-dir", "w", *bad] if "--work-dir" not in bad else bad)
    assert script._args(["--work-dir", "w", "--dry"]).dry is True
    assert script._args(["--work-dir", "w", "--live", "--env-file", "e"]).live is True


def test_defaults_are_one_real_post_and_a_quarter_dollar():
    args = script._args(["--work-dir", "w", "--dry"])
    assert (args.max_real_posts, args.max_usd) == (1, 0.25)
    assert args.image_generator in script.IMAGE_GENERATORS and "z_image" not in script.IMAGE_GENERATORS


def test_live_needs_an_env_file_and_allows_exactly_one_real_submit():
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--live"])
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--live", "--env-file", "e", "--max-real-posts", "2", "--max-usd", "1"])
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--live", "--env-file", "e", "--max-real-posts", "0"])


def test_the_conservative_worst_case_must_fit_the_authorisation():
    assert script.authorisation_problem(1, 0.25) is None
    assert script.worst_case_usd(1) == script.PRICE_HINT_USD
    assert "exceeds" in script.authorisation_problem(3, 0.25)
    assert script.authorisation_problem(1, 0) is not None and script.authorisation_problem(0, 1) is not None
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--dry", "--max-usd", "0.05"])


def test_real_decision_is_a_live_only_option():
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--dry", "--real-decision"])
    assert script._args(["--work-dir", "w", "--live", "--env-file", "e", "--real-decision"]).real_decision is True


def test_a_kie_generator_cannot_be_chosen():
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--dry", "--image-generator", "flux_2_pro"])
    for gid in script.IMAGE_GENERATORS:
        from app.generation_options import IMAGE_GENERATORS_BY_ID
        assert IMAGE_GENERATORS_BY_ID[gid].short_desc.startswith("Outsee") or gid.startswith(("gpt_image", "nano_banana"))


def test_the_app_caps_admit_exactly_one_submit_estimate():
    cap = script.gate_cap_usd(0.30)
    assert 0.30 <= cap < 0.60  # one estimate passes, estimate + the recorded unknown-cost row (0.30 + 0.30) does not


# ---------- the env file: whitelist only, never echoed ----------

def test_env_file_is_read_only_by_the_whitelist(tmp_path):
    env_file = tmp_path / "e.env"
    env_file.write_text("OUTSEE_API_KEY=secret-outsee\nKIE_API_KEY=kie-secret\nELEVENLABS_API_KEY=el\nVIBECODE_API_KEY=vk\n"
                        "# c\nOUTSEE_API_BASE_URL=https://outsee.io\nOUTSEE_DEFAULT_IMAGE_MODEL=gpt-image-2\n", encoding="utf-8")
    got = script.load_env_whitelist(str(env_file), script.OUTSEE_ENV_NAMES)
    assert set(got) == set(script.OUTSEE_ENV_NAMES) and got["OUTSEE_API_KEY"] == "secret-outsee"
    assert "KIE_API_KEY" not in got and "ELEVENLABS_API_KEY" not in got and "VIBECODE_API_KEY" not in got
    with_decision = script.load_env_whitelist(str(env_file), script.OUTSEE_ENV_NAMES + script.DECISION_ENV_NAMES)
    assert with_decision["VIBECODE_API_KEY"] == "vk" and "KIE_API_KEY" not in with_decision


def test_the_report_never_contains_a_secret():
    snap = {"counters": {"paid_posts": 1, "decision_posts": 1}}
    text = json.dumps(script.reality_report("live", snap)) + json.dumps(script.reality_not_proven("live"))
    assert "secret" not in text.lower()


@pytest.mark.parametrize("base,ok", [
    ("https://outsee.io", True), ("https://api.example.org", True),
    ("http://outsee.io", False), ("https://127.0.0.1:8080", False), ("https://localhost", False),
    ("https://user:pw@outsee.io", False), ("https://outsee.io/?x=1", False)])
def test_the_outsee_base_must_be_a_plain_https_host(base, ok):
    assert (script.base_problem({"OUTSEE_API_BASE_URL": base}) is None) is ok
    assert script.base_problem({}) is None  # the default is https://outsee.io


def test_live_refuses_before_starting_anything_without_a_key_or_with_a_bad_base(tmp_path, capsys):
    no_key = tmp_path / "nokey.env"
    no_key.write_text("KIE_API_KEY=x\n", encoding="utf-8")
    assert asyncio.run(script.main(["--work-dir", str(tmp_path / "w"), "--live", "--env-file", str(no_key)])) == 3
    bad_base = tmp_path / "badbase.env"
    bad_base.write_text("OUTSEE_API_KEY=abcdefgh\nOUTSEE_API_BASE_URL=http://example.org\n", encoding="utf-8")
    assert asyncio.run(script.main(["--work-dir", str(tmp_path / "w"), "--live", "--env-file", str(bad_base)])) == 3
    no_decision_key = tmp_path / "nodec.env"
    no_decision_key.write_text("OUTSEE_API_KEY=abcdefgh\n", encoding="utf-8")
    assert asyncio.run(script.main(["--work-dir", str(tmp_path / "w"), "--live", "--env-file", str(no_decision_key),
                                    "--real-decision"])) == 3
    out = capsys.readouterr().out
    assert "nothing sent" in out and "abcdefgh" not in out
    assert not (tmp_path / "w").exists()  # nothing was created: the refusal came first


# ---------- the request Gate ----------

def _dry_gate(**kw):
    return script.Gate(mode="dry", fake_port=FAKE, **kw)


def _live_gate(**kw):
    return script.Gate(mode="live", fake_port=FAKE, outsee_host="outsee.io", phase="main", **kw)


def test_dry_lets_loopback_through_and_refuses_everything_else():
    gate = _dry_gate(phase="main")
    assert gate.judge("GET", "127.0.0.1", MAIN, "/api/projects/1", is_decision=False).action == "pass"
    assert gate.judge("POST", "127.0.0.1", MAIN, "/api/projects/1/steps/img/run", is_decision=False).action == "pass"
    for host in ("outsee.io", "vibecode.moe", "example.com", "10.0.0.5", "127.0.0.1.evil.com", None):
        assert gate.judge("GET", host, 443, "/", is_decision=False).action == "refuse"
    assert len(gate.refused) == 6 and gate.paid_posts == 0


def test_dry_counts_the_fake_submit_and_refuses_the_second():
    gate = _dry_gate(phase="main", max_paid_posts=1)
    assert gate.judge("POST", "127.0.0.1", FAKE, script.GENERATE_PATH, is_decision=False).kind == "paid_post_fake"
    second = gate.judge("POST", "127.0.0.1", FAKE, script.GENERATE_PATH, is_decision=False)
    assert second.action == "refuse" and gate.paid_posts == 1 and gate.refused_cap == 1
    assert gate.judge("GET", "127.0.0.1", FAKE, script.STATUS_PREFIX + "x", is_decision=False).action == "pass"


def test_a_submit_during_a_guard_probe_is_a_failure_and_never_a_paid_post():
    gate = _dry_gate(phase="probe")
    assert gate.judge("POST", "127.0.0.1", FAKE, script.GENERATE_PATH, is_decision=False).kind == "fake_submit_during_probe"
    assert gate.probe_posts == 1 and gate.paid_posts == 0


def test_model_requests_are_answered_only_for_a_decision():
    gate = _dry_gate(phase="main")
    assert gate.judge("POST", "127.0.0.1", MAIN, "/v1/chat/completions", is_decision=True).action == "stub_decision"
    unexpected = gate.judge("POST", "127.0.0.1", MAIN, "/v1/chat/completions", is_decision=False)
    assert unexpected.action == "refuse" and gate.unexpected_model == 1
    assert gate.judge("POST", "127.0.0.1", MAIN, "/codex/v1/responses", is_decision=True).action == "refuse"


def test_live_allows_one_paid_submit_to_the_outsee_host_and_nothing_else_paid():
    gate = _live_gate(max_paid_posts=1)
    assert gate.judge("POST", "outsee.io", 443, script.GENERATE_PATH, is_decision=False).kind == "paid_post_real"
    assert gate.judge("POST", "outsee.io", 443, script.GENERATE_PATH, is_decision=False).action == "refuse"
    assert gate.paid_posts == 1 and gate.refused_cap == 1
    for path in ("/api/v1/videos/generate", "/api/v1/models", "/api/v1/images/generate/extra", "/"):
        assert gate.judge("POST", "outsee.io", 443, path, is_decision=False).action == "refuse", path
    assert gate.judge("GET", "outsee.io", 443, "/api/v1/models", is_decision=False).action == "refuse"
    assert gate.judge("GET", "outsee.io", 443, "/api/v1/generations/42", is_decision=False).kind == "outsee_get"
    assert gate.judge("GET", "outsee.io", 443, "/api/v1/balance", is_decision=False).kind == "outsee_get"


def test_live_submit_is_refused_outside_the_main_phase_and_the_fake_is_refused_in_live():
    gate = _live_gate()
    gate.phase = "probe"
    assert gate.judge("POST", "outsee.io", 443, script.GENERATE_PATH, is_decision=False).action == "refuse"
    gate.phase = "main"
    assert gate.judge("POST", "127.0.0.1", FAKE, script.GENERATE_PATH, is_decision=False).action == "refuse"
    assert gate.paid_posts == 0


def test_live_downloads_only_from_hosts_the_provider_itself_named():
    gate = _live_gate()
    url = "https://cdn.outsee-files.example/results/abc.png?sig=1"
    assert gate.judge("GET", "cdn.outsee-files.example", 443, "/results/abc.png", is_decision=False).action == "refuse"
    gate.learn(json.dumps({"status": "completed", "result_urls": [url]}))
    assert gate.judge("GET", "cdn.outsee-files.example", 443, "/results/abc.png", is_decision=False).kind == "download_get"
    assert gate.judge("POST", "cdn.outsee-files.example", 443, "/anything", is_decision=False).action == "refuse"
    assert gate.judge("GET", "evil.example", 443, "/x.png", is_decision=False).action == "refuse"
    assert script.hosts_in_text("see https://a.example/x and http://insecure.example/y") == {"a.example"}


def test_live_without_a_real_decision_refuses_every_vibecode_request():
    gate = _live_gate()
    assert gate.judge("POST", "vibecode.moe", 443, "/v1/chat/completions", is_decision=True).action == "refuse"
    assert gate.judge("POST", "vibecode.moe", 443, "/v1/chat/completions", is_decision=False).action == "refuse"


def test_a_real_decision_is_allowed_for_decision_requests_only_and_capped():
    gate = _live_gate(real_decision=True, decision_hosts=frozenset({"vibecode.moe"}), max_decision_posts=2)
    assert gate.judge("POST", "vibecode.moe", 443, "/v1/chat/completions", is_decision=True).action == "real_decision"
    assert gate.judge("POST", "vibecode.moe", 443, "/v1/chat/completions", is_decision=False).action == "refuse"
    assert gate.judge("POST", "api.kie.ai", 443, "/codex/v1/responses", is_decision=True).action == "refuse"
    assert gate.judge("POST", "vibecode.moe", 443, "/v1/chat/completions", is_decision=True).action == "real_decision"
    third = gate.judge("POST", "vibecode.moe", 443, "/v1/chat/completions", is_decision=True)
    assert third.action == "refuse" and gate.decision_posts == 2 and gate.refused_cap == 1


def test_balance_helpers_only_use_plain_numbers():
    assert script.numeric_fields({"balance": 10.5, "currency": "USD", "flag": True, "n": 3}) == {"balance": 10.5, "n": 3.0}
    assert script.numeric_fields("nope") == {}
    assert script.balance_delta({"balance": 10.0, "x": 1.0}, {"balance": 9.97, "x": 1.0}) == {"balance": 0.03}


# ---------- the fake Outsee ----------

def _req(url, *, key=script.FAKE_KEY, body=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"} if key else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_the_fake_outsee_plays_submit_status_and_download_and_counts():
    fake = script.FakeOutsee(18779).start()
    try:
        assert _req(fake.base_url + script.GENERATE_PATH, key=None, body={"prompt": "x"})[0] == 401
        assert _req(fake.base_url + script.GENERATE_PATH, key="wrong", body={"prompt": "x"})[0] == 401
        assert fake.auth_failures == 2 and fake.posts == 0
        status, raw = _req(fake.base_url + script.GENERATE_PATH, body={"prompt": "p", "model": "gpt-image-2"})
        gen = json.loads(raw)
        assert status == 200 and gen["id"] == "fake-0001" and fake.posts == 1
        status, raw = _req(fake.base_url + script.STATUS_PREFIX + gen["id"])
        done = json.loads(raw)
        assert status == 200 and done["status"] == "completed" and fake.polls == 1
        status, png = _req(done["result_urls"][0], key=None)
        assert status == 200 and fake.downloads == 1 and png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 200_000
        assert json.loads(_req(fake.base_url + script.BALANCE_PATH)[1])["balance"] == 10.0
        assert _req(fake.base_url + "/nothing")[0] == 404
        assert fake.counts() == {"posts": 1, "polls": 1, "downloads": 1, "auth_failures": 2}
    finally:
        fake.stop()


def test_the_fake_picture_passes_the_native_scene_checks(tmp_path):
    from app.services.scan_frames import is_valid_scene_image
    path = tmp_path / "frame_001_ab12cd34.png"
    path.write_bytes(script.make_png())
    assert is_valid_scene_image(path)


# ---------- environment ----------

def test_environment_is_synthetic_loopback_with_the_fake_provider(tmp_path):
    env = script.env_overrides(tmp_path, MAIN, fake_base="http://127.0.0.1:18769", gate_cap=0.45, dry=True)
    assert env["OUTSEE_API_BASE_URL"] == "http://127.0.0.1:18769" and env["OUTSEE_API_KEY"] == script.FAKE_KEY
    assert env["VIBECODE_API_KEY"].startswith("synthetic") and env["IMAGE_PROVIDER"] == "outsee"
    assert env["KIE_API_KEY"] == "" and env["ELEVENLABS_API_KEY"] == ""
    assert not any(k in env for k in ("GPT_RELAY_TOKEN", "GPT_PROXY_URL"))
    assert {env["SPEND_DAILY_CAP_USD"], env["SPEND_PROJECT_CAP_USD"], env["SPEND_RUN_CAP_USD"]} == {"0.45"}
    assert float(env["DISK_GUARD_LOW_FREE_GB"]) < 0.01  # dry: not machine dependent
    live = script.env_overrides(tmp_path, MAIN, fake_base="http://127.0.0.1:18769", gate_cap=0.45, dry=False)
    assert "DISK_GUARD_LOW_FREE_GB" not in live  # live: the production thresholds stay


def test_runtime_config_is_the_loopback_decision_by_default():
    cfg = script._runtime_config(MAIN, 60, None)
    assert cfg["endpoint"].startswith("http://127.0.0.1:") and "retry_same" in cfg["allowed_actions"]
    assert script._runtime_config(MAIN, 60, "https://vibecode.moe/v1/chat/completions")["endpoint"].startswith("https://")


def test_the_node_is_the_images_node_with_its_native_step_code():
    from app.orchestrator.node_registry import WORK_NODES
    from app.services.node_capabilities import capability_for
    assert WORK_NODES[script.NODE].step_code == script.STEP_CODE
    assert "retry_same" in capability_for(script.NODE).actions
    from app.services import media_local_resume
    assert media_local_resume.admitted(script.NODE)


# ---------- checks ----------

def _good_snapshot(**over):
    ticket = {"status": "verified", "incident_id": "inc-1",
              "preconditions": {"action_type": "retry_same", "media_operation": "resume_local",
                                "submits_new_paid_request": False},
              "verification_result": {"verified": True, "problems": [], "new_paid_requests": 0, "new_provider_task_ids": [],
                                      "node_started": False, "semantic_quality_verified": False,
                                      "files": [{"key": "frame_001", "path": "scenes\\frame_001_ab12cd34.png", "status": "passed"}]}}
    blocked = {"blocked": True, "posts_delta": 0}
    snap = {
        "counters": {"paid_posts": 1, "probe_posts": 0, "decision_posts": 0, "refused_cap": 0, "unexpected_model": 0,
                     "injected_hitl": 1, "injected_tail": 8, "fake_posts": 1, "fake_auth_failures": 0, "refused_foreign": 0},
        "probes": {"kill_switch": {**blocked, "code": "kill_switch_active"}, "disk_guard": {**blocked, "code": "disk_guard_blocked"},
                   "spend_cap": {**blocked, "code": "spend_cap_daily"}, "second_submit_cap": {**blocked, "code": "spend_cap_daily"},
                   "kill_switch_recovery": {"refused": True, "tickets_after": 0},
                   "identical_request": {"adopted": True, "posts_delta": 0},
                   "resume_only_other": {"refused": True, "posts_delta": 0}},
        "diagnoses": {"incidents": [{"node_key": script.TARGET, "type": "media_failure", "media": True,
                                     "incident_id": "inc-1", "status": "resolved_confirmed",
                                     "resolution": {"kind": "supervisor_recovery_verified"}}]},
        "decisions": {"records": [{"status": "done", "model_steps": 2}]},
        "actions": {"recoveries": [ticket]},
        "idem_rows": [{"event": "intent", "key": "frame_001"}, {"event": "result", "key": "frame_001", "file_sha256": "abc"}],
        "ledger_rows": [{"api": "outsee", "key": "frame_001", "event": "intent", "status": "submit_unknown"},
                        {"api": "outsee", "key": "frame_001", "event": "intent_done", "status": "bound"}],
        "spend_rows": [{"provider": "outsee", "kind": "estimated", "usd": 0.0, "note": "image gpt-image-2 ok cost_unknown=true"}],
        "recorded_file": {"exists": True, "sha256": "abc"}, "recorded_relative_path": "scenes/frame_001_ab12cd34.png",
        "node_status": {script.TARGET: "failed"}, "node_attempts": 9,
        "step_failure": {"abandoned_at": "t", "total_fails": {"generating_images": 9}},
        "decision_contexts": [{"media_recovery": {"operation": "resume_local", "submits_new_paid_request": False,
                                                  "keys": [{"key": "frame_001", "mode": "resume_local"}]}}],
        "hitl_pending": [], "refused_hosts": [],
    }
    snap.update(over)
    return snap


def test_a_good_dry_snapshot_passes_every_check():
    checks = script.evaluate(_good_snapshot(), mode="dry")
    assert script.verdict(checks) == (True, [])
    assert len(checks) >= 25 and len({c["name"] for c in checks}) == len(checks)


def test_a_good_live_snapshot_passes_and_adds_the_cap_checks():
    snap = _good_snapshot()
    snap["counters"]["fake_posts"] = None
    checks = script.evaluate(snap, mode="live")
    assert script.verdict(checks) == (True, [])
    names = {c["name"] for c in checks}
    assert {"caps_not_exceeded", "real_submit_sent"} <= names and "provider_independently_saw_one_submit" not in names


def _failed(snapshot, mode="dry"):
    return set(script.verdict(script.evaluate(snapshot, mode=mode))[1])


def test_a_second_paid_submit_fails_the_money_checks():
    snap = _good_snapshot()
    snap["counters"]["paid_posts"] = 2
    assert {"exactly_one_paid_submit_at_most", "no_second_submit_after_the_loss"} <= _failed(snap)


def test_a_recovery_that_paid_or_started_the_node_fails():
    snap = _good_snapshot()
    snap["actions"]["recoveries"][0]["verification_result"].update(new_paid_requests=1, node_started=True)
    assert "recovery_made_zero_new_paid_requests" in _failed(snap)


def test_a_missing_or_wrong_record_fails():
    snap = _good_snapshot(idem_rows=[{"event": "intent", "key": "frame_001"}])
    assert {"result_recorded_before_the_loss"} <= _failed(snap)
    consumed = _good_snapshot(idem_rows=[{"event": "intent", "key": "frame_001"},
                                         {"event": "result", "key": "frame_001", "file_sha256": "abc"},
                                         {"event": "consumed", "key": "frame_001"}])
    assert "result_recorded_before_the_loss" in _failed(consumed)
    assert "spend_ledger_written" in _failed(_good_snapshot(spend_rows=[]))
    assert "intent_records_written" in _failed(_good_snapshot(ledger_rows=[]))


def test_a_file_other_than_the_recorded_one_fails_the_resume_check():
    snap = _good_snapshot(recorded_relative_path="scenes/frame_001_other.png")
    assert "resume_read_the_recorded_result" in _failed(snap)


def test_a_guard_that_did_not_block_is_named():
    snap = _good_snapshot()
    snap["probes"]["kill_switch"] = {"blocked": False, "code": "", "posts_delta": 1}
    snap["probes"]["disk_guard"]["posts_delta"] = 1
    snap["probes"]["second_submit_cap"] = {"blocked": False, "code": "RuntimeError", "posts_delta": 0}
    snap["probes"]["kill_switch_recovery"] = {"refused": False, "tickets_after": 1}
    assert {"kill_switch_stops_the_paid_seam", "disk_guard_stops_the_paid_seam", "spend_cap_stops_a_second_submit",
            "kill_switch_stops_the_recovery"} <= _failed(snap)


def test_a_probe_that_reached_a_provider_or_a_foreign_host_fails():
    snap = _good_snapshot()
    snap["counters"]["probe_posts"] = 1
    snap["counters"]["unexpected_model"] = 1
    snap["refused_hosts"] = ["vibecode.moe"]
    assert {"no_probe_reached_a_provider", "no_unexpected_model_or_foreign_request", "dry_nothing_left_the_machine"} <= _failed(snap)


def test_a_decision_without_a_resume_local_offer_fails():
    snap = _good_snapshot(decision_contexts=[{"media_recovery": {"operation": None, "denied": "verdict_denied"}}])
    assert "decision_sees_resume_local_of_the_recorded_result" in _failed(snap)


def test_a_node_that_never_exhausted_its_native_retries_fails():
    snap = _good_snapshot(step_failure={}, node_attempts=1)
    assert {"native_retries_exhausted_and_project_paused", "outcome_loss_injected_and_native_retries_ran"} <= _failed(snap)


def test_the_verdict_never_claims_a_live_proof_for_a_dry_run():
    assert script.final_verdict("dry", True).startswith("DRY OK") and "NOT a live proof" in script.final_verdict("dry", True)
    assert script.final_verdict("live", True).startswith("PASS") and script.final_verdict("dry", False) == "NOT PASSED"
    assert "live proof" not in script.final_verdict("live", False)


def test_reality_report_separates_real_from_local():
    dry = script.reality_report("dry", {"counters": {"paid_posts": 1}})
    assert not any("real Outsee submit" in x for x in dry["real"])
    assert any("fake Outsee" in x for x in dry["not_real"]) and any("injected" in x for x in dry["not_real"])
    live = script.reality_report("live", {"counters": {"paid_posts": 1, "decision_posts": 0}})
    assert any("1 real Outsee submit" in x for x in live["real"]) and not any("fake Outsee" in x for x in live["not_real"])
    assert any("stub" in x for x in live["not_real"])
    live_decision = script.reality_report("live", {"counters": {"paid_posts": 1, "decision_posts": 2}})
    assert any("2 real Decision" in x for x in live_decision["real"]) and not any("stub" in x for x in live_decision["not_real"])
    assert any("NOT a live proof" in x for x in script.reality_not_proven("dry"))
    assert not any("NOT a live proof" in x for x in script.reality_not_proven("live"))
    assert any("task id" in x for x in dry["not_real"])  # Outsee has none: stated, not hidden


# ---------- a real dry run (whole app in a subprocess, nothing leaves the machine) ----------

def _run(tmp_path: Path, *extra: str) -> tuple[int, dict, str]:
    env = {**os.environ, "PYTHONUTF8": "1", "NO_COLOR": "1"}
    for name in ("GPT_RELAY_TOKEN", "GPT_PROXY_URL", "VIBECODE_API_KEY", "GPT_API_KEY", "OUTSEE_API_KEY", "KIE_API_KEY",
                 "ELEVENLABS_API_KEY"):
        env.pop(name, None)
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(_PATH), "--work-dir", str(tmp_path), "--dry", "--port", "18788",
         "--fake-port", "18789", "--settle-s", "3", "--sleep-minutes", "0.01", "--timeout-s", "420", *extra],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=560)
    result_path = tmp_path / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    return proc.returncode, result, (proc.stdout or "")[-1800:]


def test_a_real_dry_run_proves_the_machinery_offline(tmp_path):
    code, result, tail = _run(tmp_path)
    assert code == 0, tail
    summary, counters = result["summary"], result["counters"]
    assert result["mode"] == "dry" and result["ok"] is True, result.get("failed")
    assert summary["verdict"].startswith("DRY OK") and "NOT a live proof" in summary["verdict"]
    assert counters["paid_posts"] == 1 and counters["fake_posts"] == 1 and counters["probe_posts"] == 0
    assert counters["injected_hitl"] >= 1 and counters["injected_tail"] >= 1 and result["node_attempts"] >= 9
    assert result["refused_hosts"] == [] and counters["unexpected_model"] == 0
    ticket = result["actions"]["recoveries"][0]
    assert ticket["status"] == "verified" and ticket["preconditions"]["media_operation"] == "resume_local"
    assert ticket["verification_result"]["new_paid_requests"] == 0
    assert result["node_status"][script.TARGET] == "failed"  # the supervisor never starts the node
    assert [e[0] for e in result["idem_events"] if e[1] == "frame_001"] == ["intent", "result"]
    assert result["fake_after_probes"]["posts"] == 1
    assert all(result["probes"][k]["posts_delta"] == 0 for k in
               ("kill_switch", "disk_guard", "spend_cap", "second_submit_cap", "identical_request", "resume_only_other"))
    assert "fake" in json.dumps(result["reality"]["not_real"]) and "live" not in summary["verdict"].lower().replace("not a live proof", "")


def test_the_dry_run_refuses_a_work_dir_inside_live_data(tmp_path):
    proc = subprocess.run([sys.executable, "-X", "utf8", str(_PATH), "--work-dir", str(ROOT / "data" / "x"), "--dry"],
                          cwd=ROOT, env={**os.environ, "PYTHONUTF8": "1"}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode != 0 and not (ROOT / "data" / "x" / "result.json").exists()
