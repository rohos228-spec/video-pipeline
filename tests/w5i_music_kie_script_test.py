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
_PATH = ROOT / "scripts" / "e2e_music_kie.py"
_spec = importlib.util.spec_from_file_location("e2e_music_kie", _PATH)
script = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = script
_spec.loader.exec_module(script)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stand_workdir import make_workdir, remove_workdir

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
HOST = script.Gate(mode="live").host


@pytest.fixture
def tmp_path():
    path = make_workdir()
    try:
        yield path
    finally:
        remove_workdir(path)



def test_exactly_one_of_dry_and_live_is_required():
    for bad in (["--work-dir", "w"], ["--work-dir", "w", "--dry", "--live", "--env-file", "e"], ["--dry"]):
        with pytest.raises(SystemExit):
            script._args(bad)
    assert script._args(["--work-dir", "w", "--dry"]).dry is True
    assert script._args(["--work-dir", "w", "--live", "--env-file", "e"]).live is True


def test_defaults_are_one_real_post_and_seventy_cents():
    args = script._args(["--work-dir", "w", "--dry"])
    assert (args.max_real_posts, args.max_usd, args.dry_scenario) == (1, 0.70, "ok")


def test_live_needs_an_env_file_and_no_dry_scenario():
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--live"])
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--live", "--env-file", "e", "--dry-scenario", "task_failed"])


def test_the_authorisation_covers_the_real_post_the_price_and_the_hard_ceiling():
    assert script.authorisation_problem(1, 0.70) is None and script.authorisation_problem(2, 1.0) is None
    assert "exceeds" in script.authorisation_problem(1, 0.05)
    assert "hard ceiling" in script.authorisation_problem(1, 1.01)
    assert script.authorisation_problem(0, 0.3) is not None and script.authorisation_problem(1, 0) is not None
    for bad in (["--max-real-posts", "0"], ["--max-usd", "0.01"], ["--max-usd", "5"]):
        with pytest.raises(SystemExit):
            script._args(["--work-dir", "w", "--dry", *bad])
    assert script.worst_case_usd() == pytest.approx(script.CREDITS_PER_GENERATION_HINT * script.CREDIT_USD_HINT)
    assert script.app_cap_usd(0.70) == 0.70 and script.app_cap_usd(5) == script.HARD_CEILING_USD


def test_the_price_hints_are_the_catalogs_own_numbers():
    from app.services import kie_catalog, media_spend
    spec = kie_catalog.get_model("suno-music")
    est = kie_catalog.estimate_credits(spec, {"customMode": False, "prompt": script.MUSIC_PROMPT,
                                               "instrumental": True, "model": script.MUSIC_MODEL})
    assert est["credits"] == script.CREDITS_PER_GENERATION_HINT and kie_catalog.CREDIT_USD == script.CREDIT_USD_HINT
    assert est["usd"] == pytest.approx(script.worst_case_usd())
    assert script.worst_case_usd() < script.DEFAULT_MAX_USD <= script.HARD_CEILING_USD
    assert script.MIN_BALANCE_CREDITS > script.CREDITS_PER_GENERATION_HINT
    assert media_spend.price_kie(spec, {"model": "V5_5"})["usd"] == pytest.approx(script.worst_case_usd())


def test_the_stand_prompt_is_the_instrumental_v5_5_payload_the_gate_accepts():
    from app.services.kie_catalog import build_payload, get_model
    spec = get_model("suno-music")
    payload = build_payload(spec, {"customMode": False, "prompt": script.MUSIC_PROMPT, "instrumental": True,
                                   "model": "V5_5"})
    payload = {**payload, "callBackUrl": script.CALLBACK_PLACEHOLDER}
    assert len(script.MUSIC_PROMPT) <= script.MUSIC_PROMPT_MAX_CHARS
    assert script.Gate(mode="live").judge("POST", "https", HOST, script.CREATE_PATH, payload).action == "pass"



def test_env_file_is_read_only_by_the_whitelist(tmp_path):
    env_file = tmp_path / "e.env"
    env_file.write_text("KIE_API_KEY=secret-kie\nKIE_API_BASE_URL=https://api.kie.ai\nGPT_API_KEY=gpt-secret\n"
                        "ELEVENLABS_API_KEY=el-secret\nOUTSEE_API_KEY=out-secret\n# c\nGPT_PROXY_URL=http://x\n",
                        encoding="utf-8")
    got = script.load_env_whitelist(str(env_file))
    assert set(got) == {"KIE_API_KEY", "KIE_API_BASE_URL"} and got["KIE_API_KEY"] == "secret-kie"
    assert not any(k in got for k in ("GPT_API_KEY", "ELEVENLABS_API_KEY", "OUTSEE_API_KEY", "GPT_PROXY_URL"))


def test_the_report_never_contains_a_secret():
    text = json.dumps(script.reality_report("live", {"wire_create": 1})) + json.dumps(script.reality_not_proven("live"))
    assert "secret" not in text.lower() and "1 real Kie Suno createTask" in text
    assert "real Kie Suno createTask" not in json.dumps(script.reality_report("dry", {"wire_create": 1}))


def test_live_refuses_before_starting_anything_without_a_key_a_file_or_with_a_bad_base(tmp_path, capsys):
    no_key = tmp_path / "nokey.env"
    no_key.write_text("GPT_API_KEY=x\n", encoding="utf-8")
    assert asyncio.run(script.main(["--work-dir", str(tmp_path / "w"), "--live", "--env-file", str(no_key)])) == 3
    missing = tmp_path / "missing.env"
    assert asyncio.run(script.main(["--work-dir", str(tmp_path / "w"), "--live", "--env-file", str(missing)])) == 3
    for bad in ("http://api.kie.ai", "https://127.0.0.1", "https://api.kie.ai/codex", "ftp://example.org"):
        bad_base = tmp_path / "badbase.env"
        bad_base.write_text(f"KIE_API_KEY=abcdefgh\nKIE_API_BASE_URL={bad}\n", encoding="utf-8")
        assert asyncio.run(script.main(["--work-dir", str(tmp_path / "w"), "--live", "--env-file", str(bad_base)])) == 3
    out = capsys.readouterr().out
    assert "nothing sent" in out and "abcdefgh" not in out
    assert not (tmp_path / "w").exists()
    assert script.base_url_problem("https://api.kie.ai") is None and script.base_url_problem("https://relay.example.org/") is None


def test_the_balance_stop_rules():
    assert "unavailable" in script.balance_stop_reason(None)
    assert "too_low" in script.balance_stop_reason(0.0) and "too_low" in script.balance_stop_reason(script.MIN_BALANCE_CREDITS)
    assert script.balance_stop_reason(script.MIN_BALANCE_CREDITS + 0.01) is None



def _gate(**kw):
    return script.Gate(mode=kw.pop("mode", "live"), **kw)


def _create(gate, **kw):
    body = {"customMode": False, "prompt": script.MUSIC_PROMPT, "instrumental": True, "model": "V5_5",
            "callBackUrl": script.CALLBACK_PLACEHOLDER, **kw.pop("body", {})}
    return gate.judge(kw.pop("method", "POST"), kw.pop("scheme", "https"), kw.pop("host", HOST),
                      kw.pop("path", script.CREATE_PATH), body)


def test_only_the_kie_requests_are_let_through_everything_else_is_refused():
    gate = _gate()
    for kw in ({"host": "example.com"}, {"host": "api.kie.ai.evil.com"}, {"host": None}, {"scheme": "http"},
               {"method": "GET"}, {"path": "/api/v1/jobs/createTask"}, {"path": "/api/v1/chat/completions"},
               {"path": "/api/v1/generate/extend"}, {"host": "127.0.0.1"}):
        assert _create(gate, **kw).action == "refuse", kw
    assert gate.wire == [] and len(gate.refused) == 9
    assert _create(gate).kind == "create"


def test_one_real_post_and_the_payload_and_size_limits():
    gate = _gate(max_real_posts=1)
    assert _create(gate).action == "pass" and gate.count("create") == 1
    assert _create(gate).action == "refuse" and gate.refused[-1]["why"] == "paid_cap"
    other = _gate()
    for body, why in (({"instrumental": False}, "payload"), ({"model": "V4"}, "payload"), ({"customMode": True}, "payload"),
                      ({"prompt": "я" * (script.MUSIC_PROMPT_MAX_CHARS + 1)}, "prompt_size"), ({"prompt": " "}, "prompt_size"),
                      ({"callBackUrl": "https://evil.example/cb"}, "callback")):
        assert _create(other, body=body).action == "refuse" and other.refused[-1]["why"] == why, body
    assert other.wire == []
    assert _create(other, body={"callBackUrl": None}).action == "pass"


def test_balance_polls_and_downloads_follow_the_task_and_the_urls_the_provider_named():
    gate = _gate()
    bal = lambda: gate.judge("GET", "https", HOST, script.BALANCE_PATH)
    assert [bal().action for _ in range(script.MAX_BALANCE_READS + 1)] == ["pass"] * script.MAX_BALANCE_READS + ["refuse"]
    poll = lambda t: gate.judge("GET", "https", HOST, script.POLL_PATH, params={"taskId": t})
    assert poll("t1").action == "refuse" and gate.refused[-1]["why"] == "foreign_task"
    gate.task_id = "t1"
    assert poll("t1").action == "pass" and poll("other").action == "refuse"
    url = "https://files.example.org/a/clip.mp3"
    dl = lambda u: gate.judge("GET", "https", urlsplit_host(u), urlsplit_path(u), url=u)
    assert dl(url).action == "refuse"
    gate.learn_downloads([url, "https://files.example.org/cover.jpg"])
    assert dl(url).action == "pass" and dl("https://files.example.org/other.mp3").action == "pass"
    assert dl("https://files.example.org/cover.jpg").action == "refuse"
    assert dl("https://evil.example.com/clip.mp3").action == "refuse"
    assert gate.judge("GET", "http", "files.example.org", "/a/clip.mp3", url="http://files.example.org/a/clip.mp3").action == "refuse"
    while gate.count("download") < script.MAX_DOWNLOADS:
        assert gate.judge("GET", "https", "files.example.org", "/a/clip.mp3", url=url).action == "pass"
    assert gate.judge("GET", "https", "files.example.org", "/a/clip.mp3", url=url).action == "refuse"
    assert gate.refused[-1]["why"] == "download_cap"


def urlsplit_host(u):
    from urllib.parse import urlsplit
    return urlsplit(u).hostname


def urlsplit_path(u):
    from urllib.parse import urlsplit
    return urlsplit(u).path


def test_dry_gate_allows_only_the_loopback_port():
    gate = _gate(mode="dry", scheme="http", host="127.0.0.1", port=5555)
    assert gate.judge("GET", "http", "127.0.0.1", script.BALANCE_PATH, port=5555).action == "pass"
    assert gate.judge("GET", "http", "127.0.0.1", script.BALANCE_PATH, port=5556).action == "refuse"
    assert gate.judge("GET", "https", "api.kie.ai", script.BALANCE_PATH).action == "refuse"
    gate.learn_downloads(["https://api.kie.ai/x.mp3"])
    assert gate.judge("GET", "https", "api.kie.ai", "/x.mp3", url="https://api.kie.ai/x.mp3").action == "refuse"



def test_the_fake_server_speaks_the_kie_shapes_on_loopback_only():
    fake = script.FakeKie("ok", seconds=1.0)
    fake.start()
    try:
        assert fake.base_url.startswith("http://127.0.0.1:")
        auth = {"Authorization": f"Bearer {script.FAKE_KEY}"}
        with httpx.Client(timeout=10, trust_env=False) as client:
            assert client.get(fake.base_url + script.BALANCE_PATH, headers=auth).json()["data"] == 1000.0
            assert client.get(fake.base_url + script.BALANCE_PATH, headers={"Authorization": "Bearer other"}).json()["code"] == 401
            made = client.post(fake.base_url + script.CREATE_PATH, headers=auth, json={"prompt": "x"}).json()
            assert made["data"]["taskId"] == fake.task_id and fake.credits == 1000.0 - script.CREDITS_PER_GENERATION_HINT
            states = [client.get(fake.base_url + script.POLL_PATH, headers=auth, params={"taskId": fake.task_id}).json()["data"]
                      for _ in range(3)]
            assert [s["status"] for s in states] == ["PENDING", "FIRST_SUCCESS", "SUCCESS"]
            url = states[2]["response"]["sunoData"][0]["audioUrl"]
            assert url.startswith(fake.base_url) and client.get(url).content == fake.audio
    finally:
        fake.stop()


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")
def test_the_fake_audio_is_measurable_and_longer_than_the_minimum(tmp_path):
    path = tmp_path / "x.mp3"
    path.write_bytes(script.make_audio(8.0))
    assert script.probe_duration(path) > script.MIN_DURATION_S
    assert script.probe_duration(tmp_path / "missing.mp3") is None


def test_environment_is_scratch_with_a_fake_key_and_no_other_provider(tmp_path):
    env = script.env_overrides(tmp_path, cap=0.3, dry=True)
    assert env["KIE_API_KEY"] == script.FAKE_KEY and env["KIE_API_BASE_URL"].startswith("http://127.0.0.1")
    assert env["DATA_DIR"] == str(tmp_path) and Path(env["SQLITE_PATH"]).parent == tmp_path
    assert env["MEDIA_SPEND_RECORDING"] == "true"
    assert not any(env[k] for k in ("GPT_API_KEY", "GPT_RELAY_TOKEN", "ELEVENLABS_API_KEY", "OUTSEE_API_KEY", "VIBECODE_API_KEY"))
    assert {env["SPEND_DAILY_CAP_USD"], env["SPEND_PROJECT_CAP_USD"], env["SPEND_RUN_CAP_USD"]} == {"0.3"}
    assert float(env["DISK_GUARD_LOW_FREE_GB"]) < 0.01
    assert "DISK_GUARD_LOW_FREE_GB" not in script.env_overrides(tmp_path, cap=0.3, dry=False)


def test_the_verdict_never_claims_a_live_proof_for_a_dry_run():
    assert script.final_verdict("dry", True).startswith("DRY OK") and "NOT a live proof" in script.final_verdict("dry", True)
    assert script.final_verdict("live", True).startswith("PASS") and script.final_verdict("dry", False) == "NOT PASSED"
    assert script.final_verdict("live", False) == "NOT PASSED"
    assert any("NOT a live proof" in x for x in script.reality_not_proven("dry"))
    assert not any("NOT a live proof" in x for x in script.reality_not_proven("live"))
    assert any("uncalibrated" in x for x in script.reality_not_proven("live"))
    assert script.verdict([script.check("a", True), script.check("b", False)]) == (False, ["b"])



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


def test_a_real_dry_run_proves_the_music_node_offline(tmp_path):
    code, result, tail = _run(tmp_path)
    assert code == 0, tail
    assert result["mode"] == "dry" and result["ok"] is True, result.get("failed")
    summary, counters = result["summary"], result["counters"]
    assert summary["verdict"].startswith("DRY OK") and "NOT a live proof" in summary["verdict"]
    assert (counters["wire_create"], counters["real_posts"], counters["refused"]) == (1, 0, 0)
    assert counters["wire_balance"] == 2 and counters["wire_poll"] >= 3 and counters["wire_download"] == 1
    names = {c["name"] for c in result["checks"]}
    assert len(names) == len(result["checks"]) >= 12
    assert {"balance_read_before_any_paid_call", "mp3_exists_and_is_not_empty", "artifact_music_row_points_to_the_file",
            "spend_record_written_and_correlated", "balance_read_after_and_delta_reported",
            "exactly_one_create_post_on_the_wire"} <= names
    facts = result["facts"]
    assert facts["credits_delta"] == script.CREDITS_PER_GENERATION_HINT and facts["bytes"] > 1000
    assert facts["billed_usd_by_balance"] == pytest.approx(script.worst_case_usd())
    rows = result["spend_rows"]
    assert any(r["note"].startswith("create suno suno-music task=") and r["usd"] > 0 for r in rows)
    assert all(r["provider"] == "kie" and r["project_id"] and r["run_id"] and r["request_id"] for r in rows)
    assert "fake" in json.dumps(result["reality"]["not_real"]) and "real Kie" not in json.dumps(result["reality"]["real"])
    assert result["gate_wire"][0]["kind"] == "balance" and [w["kind"] for w in result["gate_wire"]].count("create") == 1


@pytest.mark.parametrize("scenario,reason", [("low_balance", "balance_too_low"), ("balance_missing", "balance_unavailable"),
                                             ("balance_error", "balance_unavailable")])
def test_a_missing_or_low_balance_stops_before_any_paid_call(tmp_path, scenario, reason):
    code, result, tail = _run(tmp_path, "--dry-scenario", scenario)
    assert code == 1, tail
    assert result["ok"] is False and result["summary"]["verdict"] == "NOT PASSED"
    assert reason in result["summary"]["reason"] and result["stopped_before_paid_call"].startswith(reason)
    assert result["counters"]["wire_create"] == 0 and result["counters"]["wire_poll"] == 0
    assert "stopped_without_any_paid_post" in {c["name"] for c in result["checks"] if c["ok"]}
    assert result["spend_rows"] == []


@pytest.mark.parametrize("scenario,error", [("task_failed", "generation failed"), ("create_rejected", "submit rejected")])
def test_a_failed_task_or_rejected_submit_is_not_passed_and_never_resubmits(tmp_path, scenario, error):
    code, result, tail = _run(tmp_path, "--dry-scenario", scenario)
    assert code == 1, tail
    assert result["ok"] is False and result["summary"]["verdict"] == "NOT PASSED"
    assert error in result["error"] and result["counters"]["wire_create"] == 1 and result["counters"]["refused"] == 0
    assert "step_finished_without_error" in result["failed"]
    assert any(r["note"].startswith("create suno") for r in result["spend_rows"]) or scenario == "create_rejected"


@pytest.mark.slow
def test_the_dry_run_refuses_a_work_dir_inside_live_data():
    proc = subprocess.run([sys.executable, "-X", "utf8", str(_PATH), "--work-dir", str(ROOT / "data" / "x"), "--dry"],
                          cwd=ROOT, env={**os.environ, "PYTHONUTF8": "1"}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode != 0 and not (ROOT / "data" / "x" / "result.json").exists()


@pytest.mark.slow
def test_the_script_without_dry_or_live_refuses(tmp_path):
    proc = subprocess.run([sys.executable, "-X", "utf8", str(_PATH), "--work-dir", str(tmp_path / "w")],
                          cwd=ROOT, env={**os.environ, "PYTHONUTF8": "1"}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 2 and not (tmp_path / "w").exists()
