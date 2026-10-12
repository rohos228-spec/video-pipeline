"""Tests of the pure part of ``scripts/e2e_action_layer_paid_exec.py`` (F09). No server, no network, nothing paid."""
from __future__ import annotations

import importlib.util
import json
import re
import socket
import time
import urllib.request
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "e2e_action_layer_paid_exec.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("e2e_action_layer_paid_exec_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ───────── authorisation, caps, gate ─────────

def test_authorisation_hard_ceiling_and_price_hint(mod):
    assert mod.authorisation_problem(3, 0.30) is None
    assert mod.authorisation_problem(1, 1.00) is None
    assert "hard ceiling" in mod.authorisation_problem(1, 1.01)
    assert mod.authorisation_problem(4, 0.30) is not None          # 4 x $0.10 > $0.30
    assert mod.authorisation_problem(0, 0.30) is not None and mod.authorisation_problem(1, 0) is not None


def test_app_caps_cover_the_app_own_estimate_and_never_go_below_max_usd(mod):
    assert mod.app_cap_usd(0.0, 0.30) == 0.35            # 0.30 estimate of ONE submit + margin
    assert mod.app_cap_usd(0.10, 0.30) == 0.45
    assert mod.app_cap_usd(0.20, 0.30) == 0.55
    assert mod.app_cap_usd(0.0, 0.80) == 0.80            # max-usd wins when larger
    assert mod.app_cap_usd(0.70, 0.30) is None           # does not fit under the $1.00 ceiling


def test_gate_posts_one_per_scenario_two_only_in_the_second_submit_control(mod):
    assert mod.gate_posts(3, "none") == 1 and mod.gate_posts(3, "no_stop_on_kill") == 1
    assert mod.gate_posts(3, "second_submit") == 2


def test_constants_follow_the_production_code(mod):
    from app.services import kie_catalog, spend_guard
    from app.services.action_layer import builtin_montage_more as bmm
    assert mod.MEDIA_SUBMIT_ESTIMATE_USD == spend_guard.MEDIA_SUBMIT_ESTIMATE_USD
    assert mod.CREDIT_USD == kie_catalog.CREDIT_USD
    assert abs(mod.LLM_OP_CALL_HIGH_CREDITS - bmm.LLM_OP_CALL_HIGH) < 0.01
    assert mod.IMAGE_BILLED_ATTEMPTS == bmm.IMAGE_BILLED_ATTEMPTS
    # the stand's estimate of S1b equals the real estimator at the injected tariff
    est = bmm._media_estimate({"image_generator": "nano_banana_2", "image_resolution": "1k"}, 1, 1, 0, note="x") \
        if False else None
    assert est is None
    assert mod.apply_estimate_credits(10.0) == round(3 * 10.0 + bmm.LLM_OP_CALL_HIGH, 2)


def test_injected_tariff_makes_the_real_estimator_known(mod, monkeypatch):
    from app.services.action_layer import builtin_montage_more as bmm
    monkeypatch.setattr(bmm, "_kie_unit", lambda kind, g, r: (10.0, "stand"))
    est = bmm._apply_estimate({"image_generator": "nano_banana_2", "image_resolution": "1k",
                               "max_image_ops": 1, "max_video_ops": 0})
    assert est.known and abs(est.high - mod.apply_estimate_credits(10.0)) < 0.011


def test_shipped_layer_has_no_outsee_tariff(mod):
    """S1a depends on it: if this turns false the stand's expectation of 'unknown' must change."""
    from app.services.action_layer import builtin_montage_more as bmm
    unit, why = bmm._kie_unit("image", "nano_banana_2", "1k")
    assert unit is None and "Outsee" in why
    est = bmm._apply_estimate({"image_generator": "nano_banana_2", "image_resolution": "1k",
                               "max_image_ops": 1, "max_video_ops": 0})
    assert est.known is False


# ───────── wire, ledger ─────────

def test_wire_counters(mod, tmp_path):
    p = tmp_path / "wire.jsonl"
    assert mod.read_wire(p) == [] and mod.wire_posts([]) == 0
    mod.wire_append(p, {"event": "post_sent", "n": 1})
    w = mod.read_wire(p)
    assert mod.wire_posts(w) == 1 and mod.wire_in_flight(w) == 1 and mod.wire_responses(w) == 0
    mod.wire_append(p, {"event": "response_received", "status": 200})
    w = mod.read_wire(p)
    assert mod.wire_in_flight(w) == 0
    mod.wire_append(p, {"event": "post_sent", "n": 2})
    mod.wire_append(p, {"event": "post_failed", "error": "CancelledError"})
    mod.wire_append(p, {"event": "refused", "kind": "foreign", "host": "x"})
    w = mod.read_wire(p)
    assert mod.wire_posts(w) == 2 and mod.wire_in_flight(w) == 0 and len(mod.wire_refused(w)) == 1
    p.write_text(p.read_text(encoding="utf-8") + "not json\n", encoding="utf-8")
    assert {"event": "unreadable_line"} in mod.read_wire(p)


def _row(**kw):
    base = {"kind": "reserve", "provider": "", "project_id": 1, "run_id": "plan:abc", "source": "action_layer",
            "usd": 0.1, "credits": 20.0, "note": ""}
    base.update(kw)
    return base


def test_ledger_summary_open_reserve_with_unknown_native_row(mod):
    rows = [_row(), _row(kind="estimated", provider="outsee", source="media_native", usd=0.0, credits=0.0,
                         run_id="native:1:ff", note="image nano-banana-2 ok cost_unknown=true")]
    led = mod.ledger_summary(rows, "abc", 1)
    assert led["open_reserve"] is True and led["native_rows"] == 1 and led["native_unknown_rows"] == 1
    assert led["native_tagged_with_plan"] == 0 and led["native_run_ids"] == ["native:1:ff"]
    assert led["reserve_credits"] == 20.0 and led["layer_net_credits"] == 20.0


def test_ledger_summary_closed_reserve_native_counted(mod):
    rows = [_row(), _row(kind="settle", usd=-0.1, credits=-20.0, note="basis=closes_reserve"),
            _row(kind="actual", provider="outsee", source="media_native", usd=0.03, credits=6.0, note="image ok")]
    rows[2]["run_id"] = "plan:abc"
    led = mod.ledger_summary(rows, "abc", 1)
    assert led["open_reserve"] is False and led["layer_net_usd"] == 0.0
    assert led["native_credits"] == 6.0 and led["native_tagged_with_plan"] == 1
    assert mod.credits_agree(6.0, led["native_credits"]) and not mod.credits_agree(None, 6.0)
    assert not mod.credits_agree(5.0, 6.0)


def test_ledger_summary_ignores_other_plans_and_events(mod):
    rows = [_row(run_id="plan:other"), _row(kind="event", source="paid_gate_block", usd=0.0, run_id="plan:abc")]
    led = mod.ledger_summary(rows, "abc", 1)
    assert led["layer_rows"] == 0 and led["native_rows"] == 0 and led["open_reserve"] is False


# ───────── verdicts and honest lists ─────────

def test_final_verdict_wording(mod):
    assert mod.final_verdict("dry", False) == "NOT PASSED" and mod.final_verdict("live", False) == "NOT PASSED"
    assert "NOT a live proof" in mod.final_verdict("dry", True)
    assert mod.final_verdict("live", True).startswith("PASS")


def test_reality_lists_say_what_dry_cannot_prove(mod):
    dry = mod.reality_lists("dry", {"wire_posts_total": 3, "tariff_injected": True})
    assert any("loopback" in x for x in dry["not_real"]) and any("dry run" in x for x in dry["not_proven"])
    assert any("tariff" in x and "injected" in x for x in dry["not_real"])
    assert any("improve_scene" in x for x in dry["not_proven"]) and any("uncalibrated" in x for x in dry["not_proven"])
    live = mod.reality_lists("live", {"wire_posts_total": 2, "tariff_injected": False})
    assert not any("dry run" in x for x in live["not_proven"])
    assert any("real provider" in x for x in live["real"]) and not any("tariff" in x for x in live["not_real"])


def test_paid_report_skip_is_not_a_failure(mod):
    r = mod.PaidReport()
    r.skip("S4 x", "why")
    assert r.failed == [] and r.checks[0]["skipped"] is True
    r.check("known", False, known="F09-D1")
    assert r.failed == [] and r.known_defects == {"F09-D1": ["known"]}
    assert mod.PaidReport(strict=True).check("k", False, known="F09-D1") is False


# ───────── arguments and refusals ─────────

def test_parse_args_refusals(mod):
    for argv in ([], ["--dry", "--live", "--work-dir", "w"], ["--dry"], ["--live", "--work-dir", "w"],
                 ["--live", "--env-file", "e", "--work-dir", "w", "--dry-scenario", "second_submit"],
                 ["--dry", "--work-dir", "w", "--max-usd", "1.01"],
                 ["--dry", "--work-dir", "w", "--max-real-posts", "4", "--max-usd", "0.30"],
                 ["--dry", "--work-dir", "w", "--scenarios", "S9"],
                 ["--live", "--env-file", "e", "--work-dir", "w", "--max-real-posts", "2", "--max-usd", "0.30"]):
        with pytest.raises(SystemExit) as ei:
            mod.parse_args(argv)
        assert ei.value.code == 2, argv


def test_parse_args_defaults_and_tariff_policy(mod):
    a = mod.parse_args(["--dry", "--work-dir", "w"])
    assert a.max_real_posts == 3 and a.max_usd == 0.30 and a.scenario_list == ["S1", "S2", "S3"]
    assert a.inject_tariff_credits == mod.DEFAULT_TARIFF_CREDITS and a.image_generator == "nano_banana_2"
    b = mod.parse_args(["--live", "--env-file", "e", "--work-dir", "w"])
    assert b.inject_tariff_credits == 0.0                 # a paid apply_queue needs an explicit owner decision
    c = mod.parse_args(["--live", "--env-file", "e", "--work-dir", "w", "--inject-tariff-credits", "10"])
    assert c.inject_tariff_credits == 10.0
    d = mod.parse_args(["--dry", "--work-dir", "w", "--dry-scenario", "no_stop_on_kill", "--with-improve-scene"])
    assert d.dry_scenario == "no_stop_on_kill" and d.with_improve_scene is True


def test_main_refuses_live_without_key_or_with_bad_base(mod, tmp_path, capsys):
    work = str(tmp_path / "w")
    env = tmp_path / "e.txt"
    env.write_text("SOMETHING_ELSE=1\nOUTSEE_API_BASE_URL=https://outsee.example\n", encoding="utf-8")
    assert mod.main(["--live", "--env-file", str(env), "--work-dir", work]) == 3
    assert "OUTSEE_API_KEY" in capsys.readouterr().out
    env.write_text("OUTSEE_API_KEY=abcdef123456\nOUTSEE_API_BASE_URL=http://outsee.example\n", encoding="utf-8")
    assert mod.main(["--live", "--env-file", str(env), "--work-dir", work]) == 3
    assert "https" in capsys.readouterr().out
    env.write_text("OUTSEE_API_KEY=abcdef123456\nOUTSEE_API_BASE_URL=https://127.0.0.1\n", encoding="utf-8")
    assert mod.main(["--live", "--env-file", str(env), "--work-dir", work]) == 3
    assert mod.main(["--live", "--env-file", str(tmp_path / "missing.txt"), "--work-dir", work]) == 3
    assert not Path(work).exists()


def test_main_refuses_work_dir_inside_repo_or_not_empty(mod, outside_tmp, capsys):
    tmp_path = outside_tmp
    assert mod.main(["--dry", "--work-dir", str(mod.ROOT / "data" / "f09_should_not_exist")]) == 3
    assert not (mod.ROOT / "data" / "f09_should_not_exist").exists()
    (tmp_path / "old.txt").write_text("x")
    assert mod.main(["--dry", "--work-dir", str(tmp_path)]) == 3
    assert "not empty" in capsys.readouterr().err


def test_server_env_is_keyless_synthetic_and_loopback(mod, outside_tmp):
    tmp_path = outside_tmp
    cfg = {"work": str(tmp_path), "mode": "dry", "gate_posts": 1, "fake_port": 1, "app_cap_usd": 0.45, "port": 18999,
           "outsee_base": "http://127.0.0.1:1", "outsee_host": "", "seam": "none"}
    env = mod.server_env(tmp_path, 18999, cfg, "", dry=True)
    assert env["WEB_HOST"] == "127.0.0.1" and env["WEB_PORT"] == "18999" and env["TELEGRAM_ENABLED"] == "false"
    assert "OUTSEE_API_KEY" not in env and "AL_PAID_OUTSEE_KEY" not in env and "KIE_API_KEY" not in env
    assert env["VIBECODE_API_KEY"] == mod.qc.SYNTHETIC_KEY and env["VIBECODE_BASE_URL"].startswith("http://127.0.0.1:")
    assert env["SPEND_DAILY_CAP_USD"] == env["SPEND_PROJECT_CAP_USD"] == env["SPEND_RUN_CAP_USD"] == "0.45"
    assert env["MEDIA_SPEND_RECORDING"] == "true" and env["IMAGE_PROVIDER"] == "outsee"
    assert not mod.loc.inside_repo(Path(env["SQLITE_PATH"]))
    live = mod.server_env(tmp_path, 18999, cfg, "k" * 12, dry=False)
    assert live["AL_PAID_OUTSEE_KEY"] == "k" * 12 and "DISK_GUARD_LOW_FREE_GB" not in live


def test_scenario_cfg_live_allows_only_the_outsee_host_and_seams_stay_in_their_scenario(mod):
    a = mod.parse_args(["--dry", "--work-dir", "w", "--dry-scenario", "second_submit"])
    a.live_env = {}
    s1 = mod.scenario_cfg(a, "S1", Path("w/s1"), 0.5)
    s2 = mod.scenario_cfg(a, "S2", Path("w/s2"), 0.5)
    assert s1["seam"] == "second_submit" and s1["gate_posts"] == 2 and s2["seam"] == "none" and s2["gate_posts"] == 1
    assert s1["outsee_base"] == f"http://127.0.0.1:{a.fake_port}" and s1["outsee_host"] == ""
    b = mod.parse_args(["--dry", "--work-dir", "w", "--dry-scenario", "no_stop_on_kill"])
    b.live_env = {}
    assert mod.scenario_cfg(b, "S3", Path("w/s3"), 0.5)["seam"] == "no_stop_on_kill"
    assert mod.scenario_cfg(b, "S1", Path("w/s1"), 0.5)["seam"] == "none"
    c = mod.parse_args(["--live", "--env-file", "e", "--work-dir", "w"])
    c.live_env = {"OUTSEE_API_KEY": "abcdef123456", "OUTSEE_API_BASE_URL": "https://api.outsee.example"}
    cfg = mod.scenario_cfg(c, "S2", Path("w/s2"), 0.45)
    assert cfg["mode"] == "live" and cfg["outsee_host"] == "api.outsee.example" and cfg["outsee_base"] == "https://api.outsee.example"


def test_scenario_reserve_matches_the_ceilings(mod):
    a = mod.parse_args(["--dry", "--work-dir", "w"])
    assert mod.scenario_reserve(a, "S2") == mod.reserve_usd(mod.S2_CEILING_CREDITS) == 0.1
    assert mod.scenario_reserve(a, "S3") == 0.2
    assert mod.scenario_reserve(a, "S1") == mod.reserve_usd(mod.apply_estimate_credits(10.0))
    b = mod.parse_args(["--live", "--env-file", "e", "--work-dir", "w"])
    assert mod.scenario_reserve(b, "S1") == 0.0
    for s in ("S1", "S2", "S3"):
        assert mod.app_cap_usd(mod.scenario_reserve(a, s), 0.30) <= mod.HARD_CEILING_USD


# ───────── fake provider with delay ─────────

def test_slow_fake_delays_the_submit_and_counts_it(mod):
    port = _free_port()
    fake = mod.SlowFake(port, delay_s=0.4).start()
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/images/generate", data=b'{"prompt":"x"}', method="POST",
                                     headers={"Authorization": f"Bearer {mod.prod.FAKE_KEY}", "Content-Type": "application/json"})
        t0 = time.time()
        body = json.loads(urllib.request.urlopen(req, timeout=10).read())
        assert time.time() - t0 >= 0.4 and body["status"] == "queued" and fake.posts == 1
    finally:
        fake.inner.stop()


# ───────── catalog expectations the stand relies on ─────────

def test_scenario_actions_exist_with_expected_tiers():
    from app.services.action_layer.spec import ensure_builtin_actions, registry
    ensure_builtin_actions()
    paid = {"montage.apply_queue": "destructive", "step.run_step": "destructive"}
    for aid, tier in paid.items():
        spec = registry.find(aid)
        assert spec is not None and spec.risk == tier and spec.spends_money is True and spec.needs_approval, aid
        assert spec.stop is not None and spec.poll is not None, aid
    free = {"montage.queue_ops": "local_safe", "montage.get_cost_basis": "read", "step.get_run_cost_basis": "read",
            "montage.set_voiceover": "local_safe", "history.rollback": "destructive"}
    for aid, tier in free.items():
        spec = registry.find(aid)
        assert spec is not None and spec.risk == tier and spec.spends_money is False, aid
    schema = registry.find("step.run_step").params_schema["properties"]
    assert {"count", "image_generator", "image_resolution", "ceiling_credits"} <= set(schema)
    assert "ceiling_credits" not in registry.find("montage.apply_queue").params_schema["properties"]


def test_stand_seams_target_names_that_exist(mod):
    from app.bots import outsee_http
    from app.services.action_layer import builtin_montage_more as bmm, executor
    assert hasattr(outsee_http, "_post_generate") and hasattr(outsee_http, "_post_generate_via_curl")
    assert hasattr(outsee_http, "_download_via_curl") and hasattr(bmm, "_kie_unit") and hasattr(executor, "_kill_switch_on")
    assert "/api/v1/images/generate" == mod.prod.GENERATE_PATH


# ───────── the source stays free of secrets, .env and git ─────────

def test_script_source_is_safe():
    src = SCRIPT.read_text(encoding="utf-8")
    assert "dotenv" not in src.lower() and "open('.env'" not in src and '".env"' not in src
    assert not re.search(r"post\([^)]*from-text", src), "the paid planner /from-text is never called"
    for banned in ("git add", "git commit", "git stash", "git checkout"):
        assert banned not in src
    assert not re.search(r"https?://(?!127\.0\.0\.1|localhost)[a-z]", src), "no non-loopback URL in the source"
