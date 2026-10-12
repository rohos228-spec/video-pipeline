"""Guards for scripts/e2e_media_resume_poll.py (plan step F06: media.resume_poll resumes a known Kie task, it does not pay again).

Nothing here sends anything outside this machine and nothing here runs --live: the live-only paths are covered by the
argument / env-file refusals (they return before anything starts) and by the request Gate (a pure object that judges every
outgoing request). The dry runs start the stand in a SUBPROCESS (isolated memory and settings) against a fake Kie server on
127.0.0.1, so run this file in ONE pytest process.
"""
import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
_PATH = ROOT / "scripts" / "e2e_media_resume_poll.py"
_spec = importlib.util.spec_from_file_location("e2e_media_resume_poll", _PATH)
script = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = script
_spec.loader.exec_module(script)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stand_workdir import make_workdir, remove_workdir  # noqa: E402

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
TASK = "726a12e93b47b076ea5431dd0532c4fb"
HOST = script.Gate(mode="live").host


@pytest.fixture
def tmp_path():
    """Outside the repository: the e2e guard refuses a work folder inside live data."""
    path = make_workdir()
    try:
        yield path
    finally:
        remove_workdir(path)


# ---------- arguments, authorisation, refusals ----------

def test_exactly_one_of_dry_and_live_is_required():
    for bad in (["--work-dir", "w"], ["--work-dir", "w", "--dry", "--live", "--env-file", "e", "--task-id", TASK], ["--dry"]):
        with pytest.raises(SystemExit):
            script._args(bad)
    assert script._args(["--work-dir", "w", "--dry"]).dry is True
    assert script._args(["--work-dir", "w", "--live", "--env-file", "e", "--task-id", TASK]).live is True


def test_defaults_are_five_cents_and_the_ok_scenario():
    args = script._args(["--work-dir", "w", "--dry"])
    assert (args.max_usd, args.dry_scenario, args.task_id) == (0.05, "ok", None)


def test_the_ceiling_is_twenty_five_cents_and_a_zero_cap_is_refused():
    assert script.authorisation_problem(0.05) is None and script.authorisation_problem(0.25) is None
    assert "hard ceiling" in script.authorisation_problem(0.26) and script.authorisation_problem(0) is not None
    for bad in (["--max-usd", "0.3"], ["--max-usd", "0"], ["--max-usd", "-1"]):
        with pytest.raises(SystemExit):
            script._args(["--work-dir", "w", "--dry", *bad])
    assert script.app_cap_usd(0.05) == 0.05 and script.app_cap_usd(5) == script.HARD_CEILING_USD


def test_live_needs_an_env_file_a_task_id_and_no_dry_scenario():
    for bad in (["--live"], ["--live", "--env-file", "e"], ["--live", "--task-id", TASK],
                ["--live", "--env-file", "e", "--task-id", TASK, "--dry-scenario", "task_failed"]):
        with pytest.raises(SystemExit) as exc:
            script._args(["--work-dir", "w", *bad])
        assert exc.value.code == 2


def test_a_task_id_that_is_not_32_hex_characters_is_refused():
    assert script.task_id_problem(TASK) is None and script.task_id_problem(TASK.upper()) is None
    for bad in ("", None, TASK[:-1], TASK + "0", "z" * 32, "../" + TASK[:29], TASK + "\n" + TASK, "intent:" + TASK[:25]):
        assert script.task_id_problem(bad) is not None, bad
        if bad:
            with pytest.raises(SystemExit) as exc:
                script._args(["--work-dir", "w", "--dry", "--task-id", bad])
            assert exc.value.code == 2


def test_live_refuses_before_starting_anything_without_a_key_a_file_or_with_a_bad_base(tmp_path, capsys):
    base = ["--work-dir", str(tmp_path / "w"), "--live", "--task-id", TASK]
    no_key = tmp_path / "nokey.env"
    no_key.write_text("GPT_API_KEY=x\n", encoding="utf-8")
    assert asyncio.run(script.main([*base, "--env-file", str(no_key)])) == 3
    assert asyncio.run(script.main([*base, "--env-file", str(tmp_path / "missing.env")])) == 3
    for bad in ("http://api.kie.ai", "https://127.0.0.1", "https://api.kie.ai/codex", "ftp://example.org"):
        bad_base = tmp_path / "badbase.env"
        bad_base.write_text(f"KIE_API_KEY=abcdefgh\nKIE_API_BASE_URL={bad}\n", encoding="utf-8")
        assert asyncio.run(script.main([*base, "--env-file", str(bad_base)])) == 3
    out = capsys.readouterr().out
    assert "nothing sent" in out and "abcdefgh" not in out
    assert not (tmp_path / "w").exists()  # nothing was created: the refusal came first


def test_env_file_is_read_only_by_the_whitelist(tmp_path):
    env_file = tmp_path / "e.env"
    env_file.write_text("KIE_API_KEY=secret-kie\nKIE_API_BASE_URL=https://api.kie.ai\nGPT_API_KEY=gpt-secret\n"
                        "ELEVENLABS_API_KEY=el-secret\nOUTSEE_API_KEY=out-secret\n# c\n", encoding="utf-8")
    got = script.load_env_whitelist(str(env_file))
    assert set(got) == {"KIE_API_KEY", "KIE_API_BASE_URL"} and got["KIE_API_KEY"] == "secret-kie"


@pytest.mark.slow
def test_the_script_without_dry_or_live_refuses(tmp_path):
    proc = subprocess.run([sys.executable, "-X", "utf8", str(_PATH), "--work-dir", str(tmp_path / "w")],
                          cwd=ROOT, env={**os.environ, "PYTHONUTF8": "1"}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 2 and not (tmp_path / "w").exists()


# ---------- the request Gate ----------

def _gate(**kw):
    return script.Gate(mode=kw.pop("mode", "live"), task_id=kw.pop("task_id", TASK), **kw)


def _get(gate, path, **kw):
    return gate.judge("GET", kw.pop("scheme", "https"), kw.pop("host", HOST), path, **kw)


def test_any_write_to_kie_is_refused_and_counted_before_it_leaves():
    gate = _gate()
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        for path in ("/api/v1/generate", "/api/v1/jobs/createTask", script.POLL_PATH, script.BALANCE_PATH):
            assert gate.judge(method, "https", HOST, path, params={"taskId": TASK}).action == "refuse"
    assert gate.wire == [] and len(gate.write_attempts) == 16
    assert all(r["why"] == "no_writes" for r in gate.write_attempts)
    assert gate.judge("POST", "https", "example.com", "/x").action == "refuse"
    assert gate.judge("POST", "http", "127.0.0.1", "/api/v1/generate", port=5).action == "refuse"


def test_only_the_given_task_may_be_polled_and_only_a_few_times():
    gate = _gate()
    poll = lambda t: _get(gate, script.POLL_PATH, params={"taskId": t})  # noqa: E731
    assert poll(TASK).action == "pass" and poll(TASK[:-1] + "0").action == "refuse"
    assert gate.refused[-1]["why"] == "foreign_task" and poll("").action == "refuse"
    while gate.count("poll") < script.MAX_POLLS:
        assert poll(TASK).action == "pass"
    assert poll(TASK).action == "refuse" and gate.refused[-1]["why"] == "poll_cap"
    assert _gate(task_id="").judge("GET", "https", HOST, script.POLL_PATH, params={"taskId": ""}).action == "refuse"


def test_balance_reads_are_capped_and_everything_else_on_the_origin_is_refused():
    gate = _gate()
    assert [_get(gate, script.BALANCE_PATH).action for _ in range(script.MAX_BALANCE_READS + 1)] == \
        ["pass"] * script.MAX_BALANCE_READS + ["refuse"]
    for kw in ({"host": "example.com"}, {"host": "api.kie.ai.evil.com"}, {"host": None}, {"scheme": "http"},
               {"host": "127.0.0.1"}):
        assert _get(_gate(), script.BALANCE_PATH, **kw).action == "refuse", kw
    other = _gate()
    for path in ("/api/v1/chat/completions", "/api/v1/generate/extend", "/api/v1/jobs/recordInfo", "/"):
        assert _get(other, path).action == "refuse", path


def test_downloads_follow_only_the_urls_the_poll_answer_named():
    gate = _gate()
    url = "https://files.example.org/a/clip.mp3"
    dl = lambda u, scheme="https": gate.judge("GET", scheme, urlsplit_host(u), urlsplit_path(u), url=u)  # noqa: E731
    assert dl(url).action == "refuse"                                  # not named by the provider yet
    gate.learn_downloads([url, "https://files.example.org/cover.jpg", "not a url"])
    assert dl(url).action == "pass" and dl("https://files.example.org/other.mp3").action == "pass"   # same host redirect
    assert dl("https://files.example.org/cover.jpg").action == "refuse"
    assert dl("https://evil.example.com/clip.mp3").action == "refuse"
    assert dl("http://files.example.org/a/clip.mp3", "http").action == "refuse"
    while gate.count("download") < script.MAX_DOWNLOADS:
        assert dl(url).action == "pass"
    assert dl(url).action == "refuse" and gate.refused[-1]["why"] == "download_cap"
    assert gate.write_attempts == []


def urlsplit_host(u):
    from urllib.parse import urlsplit
    return urlsplit(u).hostname


def urlsplit_path(u):
    from urllib.parse import urlsplit
    return urlsplit(u).path


def test_dry_gate_allows_only_the_loopback_port_and_never_a_foreign_download():
    gate = _gate(mode="dry", scheme="http", host="127.0.0.1", port=5555)
    assert _get(gate, script.BALANCE_PATH, scheme="http", host="127.0.0.1", port=5555).action == "pass"
    assert _get(gate, script.BALANCE_PATH, scheme="http", host="127.0.0.1", port=5556).action == "refuse"
    assert _get(gate, script.BALANCE_PATH).action == "refuse"
    gate.learn_downloads(["https://api.kie.ai/x.mp3", "http://127.0.0.1:5555/f.mp3", "http://127.0.0.1:9/g.mp3"])
    assert gate.judge("GET", "https", "api.kie.ai", "/x.mp3", url="https://api.kie.ai/x.mp3").action == "refuse"
    assert gate.judge("GET", "http", "127.0.0.1", "/f.mp3", port=5555, url="http://127.0.0.1:5555/f.mp3").action == "pass"
    assert gate.judge("GET", "http", "127.0.0.1", "/g.mp3", port=9, url="http://127.0.0.1:9/g.mp3").action == "refuse"


def test_the_in_process_calls_are_limited_to_the_seeded_project():
    gate = _gate(project_id=7)
    ok = lambda m, p: gate.judge(m, "http", script.INTERNAL_HOST, p).action  # noqa: E731
    assert ok("GET", "/api/projects/7") == "pass" and ok("GET", "/api/projects/7/media-recovery/music") == "pass"
    assert ok("POST", "/api/projects/7/media-recovery/music/resume-poll") == "pass"
    assert ok("GET", "/api/projects/70") == "refuse" and ok("GET", "/api/projects/8/media-recovery/music") == "refuse"
    assert ok("GET", "/api/other") == "refuse" and gate.judge("GET", "https", script.INTERNAL_HOST, "/api/projects/7").action == "refuse"
    assert _gate().judge("GET", "http", script.INTERNAL_HOST, "/api/projects/7").action == "refuse"   # no project yet
    assert len(gate.internal) == 3 and gate.wire == []
    # an in-process POST is not a write to Kie, but a POST to the Kie host still is
    assert gate.write_attempts == [] or all(r["method"] != "POST" or r["what"].find(script.INTERNAL_HOST) < 0 for r in gate.write_attempts)


# ---------- the fake server ----------

def test_the_fake_server_speaks_the_kie_shapes_on_loopback_and_counts_any_post():
    fake = script.FakeKie("ok", TASK, seconds=1.0)
    fake.start()
    try:
        auth = {"Authorization": f"Bearer {script.FAKE_KEY}"}
        with httpx.Client(timeout=10, trust_env=False) as client:
            assert client.get(fake.base_url + script.BALANCE_PATH, headers=auth).json()["data"] == 1000.0
            assert client.get(fake.base_url + script.BALANCE_PATH, headers={"Authorization": "Bearer other"}).json()["code"] == 401
            state = client.get(fake.base_url + script.POLL_PATH, headers=auth, params={"taskId": TASK}).json()["data"]
            assert state["status"] == "SUCCESS" and state["taskId"] == TASK
            url = state["response"]["sunoData"][0]["audioUrl"]
            assert url.startswith(fake.base_url) and client.get(url).content == fake.audio
            assert client.get(fake.base_url + script.POLL_PATH, headers=auth, params={"taskId": "x"}).json()["code"] == 404
            assert fake.snapshot()["posts"] == 0 and fake.credits == 1000.0
            client.post(fake.base_url + "/api/v1/generate", headers=auth, json={"prompt": "x"})
            assert fake.snapshot()["posts"] == 1 and fake.credits < 1000.0 and fake.auth_failures == 1
    finally:
        fake.stop()


# ---------- checks (pure) ----------

def _ledger(extra=()):
    rows = [
        {"id": 1, "task_id": "intent:aa", "event": "intent", "status": "submit_unknown", "key": "music"},
        {"id": 2, "task_id": TASK, "event": "submitted", "status": "submitted", "key": "music"},
        {"id": 3, "task_id": TASK, "event": "bound", "status": "submitted", "key": "music"},
        {"id": 4, "task_id": "intent:aa", "event": "intent_done", "status": "bound", "key": "music"},
    ]
    return rows, rows + list(extra)


def _good_snapshot(**over):
    before, after = _ledger([{"id": 5, "task_id": TASK, "event": "downloaded", "status": "downloaded", "key": "music"}])
    snap = {
        "task_id": TASK, "ffprobe": True,
        "gate": {"balance": 2, "poll": 1, "download": 1, "internal": 9, "refused": [], "write_attempts": [],
                 "poll_task_ids": [TASK], "download_status": 200},
        "fake": {"posts": 0, "poll_task_ids": [TASK], "auth_failures": 0},
        "credits_before": 1000.0, "credits_after": 1000.0,
        "ledger_before": before, "ledger_after": after, "spend_before": [], "spend_after": [],
        "music_files_before": [], "music_files": [{"name": "music_ab12cd34.mp3", "bytes": 5000, "duration_s": 8.0}],
        "assessment_before": {"mode": "resume_poll", "code": "open_task", "task_id": TASK, "provider": "kie"},
        "assessment_after": {"mode": "nothing_to_do", "code": "downloaded"},
        "kill_switch": {"refused_status": 409, "refused_code": "kill_switch_active", "wire_requests_during": 0, "released": True},
        "probe": {"raised": "PaidCallBlocked", "scope_raised": "ResumeOnlyBlocked", "wire_writes_during": 0},
        "action": {"validate_problems": [], "error": None,
                   "preconditions": {"project_exists": None, "project_idle": None, "resume_available": None},
                   "result": {"operation": "resume_poll", "verified": True, "problems": [], "new_paid_requests": 0,
                              "resumed": [{"key": "music", "task_id": TASK, "outcome": "downloaded", "error": None}]}},
        "project_after": {"status": "failed", "artifacts": 0},
    }
    snap.update(over)
    return snap


def _failed(snap, mode="dry"):
    return set(script.verdict(script.evaluate(snap, mode=mode))[1])


def test_a_good_snapshot_passes_every_check_in_both_modes():
    checks = script.evaluate(_good_snapshot(), mode="dry")
    assert script.verdict(checks) == (True, []) and len({c["name"] for c in checks}) == len(checks) >= 19
    live = script.evaluate(_good_snapshot(fake=None), mode="live")
    assert script.verdict(live) == (True, []) and "provider_independently_saw_no_write_and_no_foreign_key" not in {c["name"] for c in live}


def test_a_write_on_the_wire_fails_even_when_the_gate_refused_it():
    row = {"kind": "write", "why": "no_writes", "method": "POST", "what": "POST x"}
    snap = _good_snapshot()
    snap["gate"] = {**snap["gate"], "write_attempts": [row], "refused": [row]}
    assert {"zero_writes_on_the_wire", "nothing_foreign_was_attempted"} <= _failed(snap)
    assert "zero_writes_on_the_wire" in _failed(_good_snapshot(fake={"posts": 1, "poll_task_ids": [TASK], "auth_failures": 0}))


def test_no_poll_or_a_poll_of_another_task_fails():
    snap = _good_snapshot()
    snap["gate"] = {**snap["gate"], "poll": 0, "poll_task_ids": []}
    assert "poll_get_for_exactly_the_given_task" in _failed(snap)
    snap["gate"] = {**snap["gate"], "poll": 1, "poll_task_ids": ["other"]}
    assert "poll_get_for_exactly_the_given_task" in _failed(snap)


def test_a_missing_empty_or_short_file_fails():
    assert "audio_downloaded_into_the_node_folder_and_not_empty" in _failed(_good_snapshot(music_files=[]))
    small = [{"name": "music_ab12cd34.mp3", "bytes": 10, "duration_s": 8.0}]
    assert "audio_downloaded_into_the_node_folder_and_not_empty" in _failed(_good_snapshot(music_files=small))
    short = [{"name": "music_ab12cd34.mp3", "bytes": 5000, "duration_s": 2.0}]
    assert "audio_duration_over_5_seconds" in _failed(_good_snapshot(music_files=short))
    unmeasured = [{"name": "music_ab12cd34.mp3", "bytes": 5000, "duration_s": None}]
    assert "audio_duration_over_5_seconds" in _failed(_good_snapshot(music_files=unmeasured))        # ffprobe exists: must measure
    assert "audio_duration_over_5_seconds" not in _failed(_good_snapshot(music_files=unmeasured, ffprobe=False))


def test_a_task_that_did_not_move_in_the_ledger_or_a_new_row_fails():
    before, _ = _ledger()
    assert "ledger_shows_the_same_task_moving_from_submitted_to_downloaded" in _failed(_good_snapshot(ledger_after=before))
    for extra in ({"id": 6, "task_id": "T-NEW", "event": "submitted", "status": "submitted"},
                  {"id": 6, "task_id": "intent:bb", "event": "intent", "status": "submit_unknown"},
                  {"id": 6, "task_id": TASK, "event": "bound", "status": "submitted"}):
        _, after = _ledger([{"id": 5, "task_id": TASK, "event": "downloaded", "status": "downloaded"}, extra])
        assert "ledger_has_no_new_task_id_intent_or_submit_row" in _failed(_good_snapshot(ledger_after=after)), extra


def test_a_new_spend_row_with_money_or_a_create_row_fails_but_a_zero_event_row_is_fine():
    zero = [{"id": 1, "kind": "event", "usd": 0.0, "note": "resume poll"}]
    assert "no_new_spend_row_with_money_and_no_create_row" not in _failed(_good_snapshot(spend_after=zero))
    paid = [{"id": 1, "kind": "estimated", "usd": 0.06, "note": "create suno suno-music task=T"}]
    assert "no_new_spend_row_with_money_and_no_create_row" in _failed(_good_snapshot(spend_after=paid))
    create0 = [{"id": 1, "kind": "event", "usd": 0.0, "note": "create suno rejected"}]
    assert "no_new_spend_row_with_money_and_no_create_row" in _failed(_good_snapshot(spend_after=create0))


def test_a_balance_delta_or_an_unreadable_balance_fails():
    assert "balance_delta_is_zero_credits" in _failed(_good_snapshot(credits_after=988.0))
    assert "balance_delta_is_zero_credits" in _failed(_good_snapshot(credits_after=1012.0))
    gone = _failed(_good_snapshot(credits_before=None))
    assert {"balance_read_before_and_after", "balance_delta_is_zero_credits"} <= gone


def test_the_kill_switch_probe_and_the_action_checks_fail_when_broken():
    bad_ks = {"refused_status": "not_refused", "refused_code": None, "wire_requests_during": 1, "released": False}
    assert "kill_switch_refuses_the_action_before_any_request" in _failed(_good_snapshot(kill_switch=bad_ks))
    assert "production_resume_only_scope_refuses_create_task_before_the_wire" in _failed(
        _good_snapshot(probe={"raised": None, "scope_raised": None, "wire_writes_during": 0}))
    assert "production_resume_only_scope_refuses_create_task_before_the_wire" in _failed(
        _good_snapshot(probe={"raised": "X", "scope_raised": "ResumeOnlyBlocked", "wire_writes_during": 1}))
    snap = _good_snapshot()
    snap["action"] = {**snap["action"], "preconditions": {"project_exists": None, "project_idle": "busy", "resume_available": None}}
    assert "action_validated_and_every_precondition_passed" in _failed(snap)
    snap = _good_snapshot()
    snap["action"]["result"] = {**snap["action"]["result"], "verified": False, "problems": ["music:pending"], "new_paid_requests": 1}
    assert {"action_resumed_and_production_verified_it", "production_reports_zero_new_paid_requests"} <= _failed(snap)


def test_a_seed_that_does_not_look_like_a_killed_run_fails():
    _, after = _ledger([{"id": 5, "task_id": TASK, "event": "downloaded", "status": "downloaded"}])
    assert "seed_looks_like_a_killed_run_before_the_resume" in _failed(_good_snapshot(ledger_before=after))
    assert "seed_looks_like_a_killed_run_before_the_resume" in _failed(
        _good_snapshot(music_files_before=[{"name": "music_x.mp3", "bytes": 5000}]))


def test_the_not_passed_reason_names_the_cause_and_never_a_retry():
    def reason(**over):
        return script.not_passed_reason(_good_snapshot(**over))

    snap = _good_snapshot()
    snap["gate"] = {**snap["gate"], "write_attempts": [{"method": "POST"}]}
    assert "POST" in script.not_passed_reason(snap)
    pending = _good_snapshot()
    pending["action"]["result"]["resumed"] = [{"key": "music", "task_id": TASK, "outcome": "pending", "error": None}]
    pending["poll_answers"] = [{"code": 200, "data_status": "PENDING"}]
    assert "not finished" in script.not_passed_reason(pending) and "PENDING" in script.not_passed_reason(pending)
    missing = _good_snapshot(poll_answers=[{"code": 404, "msg": "no such task"}])
    missing["action"]["result"]["resumed"] = [{"key": "music", "task_id": TASK, "outcome": None, "error": "KieHttpError"}]
    assert "no longer returns the task" in script.not_passed_reason(missing) and "404" in script.not_passed_reason(missing)
    expired = _good_snapshot(poll_answers=[{"code": 200}])
    expired["action"]["result"]["resumed"] = [{"key": "music", "task_id": TASK, "outcome": None, "error": "KieHttpError"}]
    expired["gate"] = {**expired["gate"], "download_status": 404}
    assert "expired" in script.not_passed_reason(expired)
    failed = _good_snapshot()
    failed["action"]["result"]["resumed"] = [{"key": "music", "task_id": TASK, "outcome": "failed", "error": None}]
    assert "failed" in script.not_passed_reason(failed)
    for text in (script.not_passed_reason(x) for x in (snap, pending, missing, expired, failed)):
        assert "nothing was resubmitted" in text or "Gate refused" in text
    assert reason() is None


def test_the_verdict_never_claims_a_live_proof_for_a_dry_run():
    assert script.final_verdict("dry", True).startswith("DRY OK") and "NOT a live proof" in script.final_verdict("dry", True)
    assert script.final_verdict("live", True).startswith("PASS") and script.final_verdict("dry", False) == "NOT PASSED"
    assert script.final_verdict("live", False) == "NOT PASSED"
    assert any("NOT a live proof" in x for x in script.reality_not_proven("dry"))
    assert not any("NOT a live proof" in x for x in script.reality_not_proven("live"))
    dry = script.reality_report("dry", {"wire_poll": 1})
    assert any("fake Kie" in x for x in dry["not_real"]) and not any("real Kie record-info" in x for x in dry["real"])
    live = script.reality_report("live", {"wire_poll": 1, "wire_balance": 2, "wire_download": 1})
    assert any("1 real Kie record-info" in x for x in live["real"]) and not any("fake Kie" in x for x in live["not_real"])
    assert "secret" not in (json.dumps(dry) + json.dumps(live)).lower()


def test_the_stand_drives_the_registered_action_and_never_a_create_function():
    text = _PATH.read_text(encoding="utf-8")
    assert "registry.get(ACTION_ID)" in text and "resume_spec.executor" in text
    calls = [ln.strip() for ln in text.splitlines() if "kie_http.create_task(" in ln]
    # called only by the negative-control seam and by the resume-only probe, never by the flow itself
    assert len(calls) == 2, calls


# ---------- real dry runs (the stand in a subprocess, nothing leaves the machine) ----------

def _run(tmp_path: Path, *extra: str) -> tuple[int, dict, str]:
    env = {**os.environ, "PYTHONUTF8": "1", "NO_COLOR": "1"}
    for name in ("ELEVENLABS_API_KEY", "ELEVENLABS_PROXY_URL", "KIE_API_KEY", "OUTSEE_API_KEY", "VIBECODE_API_KEY",
                 "GPT_API_KEY", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        env.pop(name, None)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    proc = subprocess.run([sys.executable, "-X", "utf8", str(_PATH), "--work-dir", str(tmp_path), "--dry", *extra],
                          cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=560)
    result_path = tmp_path / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    return proc.returncode, result, (proc.stdout or "")[-1800:] + (proc.stderr or "")[-600:]


def test_a_real_dry_run_resumes_the_known_task_and_pays_nothing(tmp_path):
    code, result, tail = _run(tmp_path, "--task-id", TASK)
    assert code == 0, tail
    assert result["mode"] == "dry" and result["ok"] is True, result.get("failed")
    summary, counters, snap = result["summary"], result["counters"], result["snapshot"]
    assert summary["verdict"].startswith("DRY OK") and "NOT a live proof" in summary["verdict"]
    assert (counters["write_attempts"], counters["wire_posts"], counters["refused"]) == (0, 0, 0)
    assert counters["wire_balance"] == 2 and counters["wire_poll"] == 1 and counters["wire_download"] == 1
    assert counters["internal_calls"] >= 5
    assert len({c["name"] for c in result["checks"]}) == len(result["checks"]) >= 19
    assert snap["fake"]["posts"] == 0 and snap["fake"]["poll_task_ids"] == [TASK]
    assert snap["credits_before"] == snap["credits_after"] == 1000.0 and snap["credits_delta"] == 0
    res = snap["action"]["result"]
    assert (res["operation"], res["verified"], res["new_paid_requests"]) == ("resume_poll", True, 0)
    assert res["resumed"][0]["task_id"] == TASK and res["resumed"][0]["outcome"] == "downloaded"
    assert snap["kill_switch"]["refused_code"] == "kill_switch_active" and snap["kill_switch"]["wire_requests_during"] == 0
    assert [(r["event"], r["status"]) for r in snap["ledger_after"] if r["task_id"] == TASK][-1] == ("downloaded", "downloaded")
    assert {r["task_id"] for r in snap["ledger_after"]} == {r["task_id"] for r in snap["ledger_before"]}
    assert snap["music_files"][0]["bytes"] > 1000 and snap["assessment_after"]["code"] == "downloaded"
    assert any("Artifact" in n for n in summary["advisories"])
    assert result["gate_wire"][0]["kind"] == "balance" and [w["kind"] for w in result["gate_wire"]] == ["balance", "poll", "download", "balance"]
    assert (tmp_path / "result.json").is_file() and not list(Path(ROOT / "data").glob("videos/music-resume-*"))


def test_the_negative_control_makes_production_try_a_create_task_and_is_not_passed(tmp_path):
    code, result, tail = _run(tmp_path, "--dry-scenario", "resume_posts_again")
    assert code == 1, tail
    assert result["ok"] is False and result["summary"]["verdict"] == "NOT PASSED"
    assert {"zero_writes_on_the_wire", "nothing_foreign_was_attempted"} <= set(result["failed"])
    assert result["counters"]["write_attempts"] == 1 and result["snapshot"]["fake"]["posts"] == 0   # the Gate stopped it
    assert result["snapshot"]["seam"]["create_task_called"] is True
    assert "POST" in result["summary"]["reason"] and "refused it before it left the machine" in result["summary"]["reason"]
    # everything else still held: the stand fails only because of the attempt
    assert result["snapshot"]["credits_delta"] == 0 and result["snapshot"]["gate"]["poll"] == 1


@pytest.mark.parametrize("scenario,reason", [("task_missing", "no longer returns the task"),
                                             ("url_expired", "not downloadable any more"),
                                             ("still_pending", "not finished"),
                                             ("task_failed", "reports the task as failed")])
def test_a_missing_expired_pending_or_failed_task_is_not_passed_and_never_posts(tmp_path, scenario, reason):
    code, result, tail = _run(tmp_path, "--dry-scenario", scenario)
    assert code == 1, tail
    assert result["ok"] is False and result["summary"]["verdict"] == "NOT PASSED"
    assert reason in result["summary"]["reason"] and "nothing was resubmitted" in result["summary"]["reason"]
    assert result["counters"]["write_attempts"] == 0 and result["snapshot"]["fake"]["posts"] == 0
    assert "zero_writes_on_the_wire" not in result["failed"] and result["snapshot"]["credits_delta"] == 0
    assert result["snapshot"]["music_files"] == []


@pytest.mark.slow
def test_the_dry_run_refuses_a_work_dir_inside_live_data():
    proc = subprocess.run([sys.executable, "-X", "utf8", str(_PATH), "--work-dir", str(ROOT / "data" / "x"), "--dry"],
                          cwd=ROOT, env={**os.environ, "PYTHONUTF8": "1"}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode != 0 and not (ROOT / "data" / "x" / "result.json").exists()
