"""Guards for scripts/e2e_media_kill_after_submit.py (plan step F05: hard kill right after the paid submit).

Nothing here sends anything outside this machine and nothing here runs --live: the live-only paths are covered by the
argument / env-file refusals (they return before anything starts) and by the shared request Gate. The dry runs start
two child pythons each (the first one is hard-killed on purpose), so run this file alone: one pytest at a time.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_PATH = ROOT / "scripts" / "e2e_media_kill_after_submit.py"
_spec = importlib.util.spec_from_file_location("e2e_media_kill_after_submit", _PATH)
script = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = script
_spec.loader.exec_module(script)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stand_workdir import make_workdir, remove_workdir  # noqa: E402


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
    for bad in (["--work-dir", "w"], ["--work-dir", "w", "--dry", "--live", "--env-file", "e"], ["--dry"]):
        with pytest.raises(SystemExit):
            script._args(bad)
    assert script._args(["--work-dir", "w", "--dry"]).dry is True
    assert script._args(["--work-dir", "w", "--live", "--env-file", "e"]).live is True


def test_defaults_are_one_real_post_and_thirty_cents():
    args = script._args(["--work-dir", "w", "--dry"])
    assert (args.max_real_posts, args.max_usd, args.dry_scenario) == (1, 0.30, "none")
    assert args.image_generator in script.prod.IMAGE_GENERATORS


def test_the_hard_ceiling_and_the_worst_case_are_enforced():
    assert script.authorisation_problem(1, 0.30) is None
    assert "hard ceiling" in script.authorisation_problem(1, 1.01)
    assert script.authorisation_problem(1, 1.0) is None
    assert "exceeds" in script.authorisation_problem(3, 0.25)
    for bad in (["--max-usd", "1.5"], ["--max-usd", "0.05"], ["--max-usd", "0"]):
        with pytest.raises(SystemExit):
            script._args(["--work-dir", "w", "--dry", *bad])


def test_live_allows_exactly_one_real_submit_and_needs_an_env_file():
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--live"])
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--live", "--env-file", "e", "--max-real-posts", "2", "--max-usd", "1"])
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--live", "--env-file", "e", "--max-real-posts", "0"])


def test_the_negative_control_is_a_dry_only_option_and_widens_only_the_gate_cap():
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--live", "--env-file", "e", "--dry-scenario", "second_submit"])
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--dry", "--dry-scenario", "nonsense"])
    assert script.posts_allowed(script._args(["--work-dir", "w", "--dry"])) == 1
    assert script.posts_allowed(script._args(["--work-dir", "w", "--dry", "--dry-scenario", "second_submit"])) == 2
    assert script.posts_allowed(script._args(["--work-dir", "w", "--live", "--env-file", "e"])) == 1


def test_a_kie_generator_cannot_be_chosen():
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--dry", "--image-generator", "flux_2_pro"])


def test_live_refuses_before_starting_anything(tmp_path, capsys):
    work = tmp_path / "w"
    missing = tmp_path / "nope.env"
    assert script.main(["--work-dir", str(work), "--live", "--env-file", str(missing)]) == 3
    no_key = tmp_path / "nokey.env"
    no_key.write_text("KIE_API_KEY=x\n", encoding="utf-8")
    assert script.main(["--work-dir", str(work), "--live", "--env-file", str(no_key)]) == 3
    bad_base = tmp_path / "badbase.env"
    bad_base.write_text("OUTSEE_API_KEY=abcdefgh\nOUTSEE_API_BASE_URL=http://example.org\n", encoding="utf-8")
    assert script.main(["--work-dir", str(work), "--live", "--env-file", str(bad_base)]) == 3
    out = capsys.readouterr().out
    assert "nothing sent" in out and "abcdefgh" not in out
    assert not work.exists()  # nothing was created: the refusal came first


def test_bad_arguments_exit_with_code_two():
    with pytest.raises(SystemExit) as exc:
        script._args(["--dry"])
    assert exc.value.code == 2


# ---------- the shared Gate: one paid post across BOTH phases ----------

def test_the_gate_preloaded_from_the_wire_refuses_a_second_paid_post_after_the_restart():
    gate = script.prod.Gate(mode="dry", fake_port=script.FAKE_PORT, phase="main", max_paid_posts=1)
    gate.paid_posts = 1  # what child 2 does: the counter comes from wire.jsonl
    second = gate.judge("POST", "127.0.0.1", script.FAKE_PORT, script.prod.GENERATE_PATH, is_decision=False)
    assert second.action == "refuse" and second.kind == "paid_cap" and gate.refused_cap == 1
    assert gate.judge("POST", "outsee.io", 443, "/api/v1/images/generate", is_decision=False).action == "refuse"


def test_the_live_gate_allows_one_paid_post_to_the_outsee_host_only():
    gate = script.prod.Gate(mode="live", outsee_host="outsee.io", fake_port=script.FAKE_PORT, phase="main", max_paid_posts=1)
    assert gate.judge("POST", "outsee.io", 443, script.prod.GENERATE_PATH, is_decision=False).kind == "paid_post_real"
    assert gate.judge("POST", "outsee.io", 443, script.prod.GENERATE_PATH, is_decision=False).action == "refuse"
    assert gate.judge("POST", "api.kie.ai", 443, "/api/v1/jobs/createTask", is_decision=False).action == "refuse"
    assert gate.judge("POST", "127.0.0.1", script.FAKE_PORT, script.prod.GENERATE_PATH, is_decision=False).action == "refuse"


# ---------- the wire file ----------

def test_wire_lines_are_durable_and_counted_per_phase(tmp_path):
    wire = tmp_path / "wire.jsonl"
    assert script.read_wire(wire) == [] and script.wire_posts([]) == 0
    script.wire_append(wire, {"event": "post_sent", "phase": 1})
    script.wire_append(wire, {"event": "response_received", "phase": 1})
    wire.open("a", encoding="utf-8").write("not json\n")
    events = script.read_wire(wire)
    assert [e["event"] for e in events] == ["post_sent", "response_received", "unreadable_line"]
    assert script.wire_posts(events) == 1 and script.wire_posts(events, 1) == 1 and script.wire_posts(events, 2) == 0


# ---------- the report ----------

def test_reality_report_never_claims_a_live_proof_for_a_dry_run():
    dry = script.reality_report("dry", {"wire_posts": 1})
    assert not any("real Outsee submit" in x for x in dry["real"])
    assert any("fake Outsee" in x for x in dry["not_real"]) and any("hard-killed" in x for x in dry["real"])
    live = script.reality_report("live", {"wire_posts": 1})
    assert any("1 real Outsee submit" in x for x in live["real"]) and not any("fake Outsee" in x for x in live["not_real"])
    assert any("NOT a live proof" in x for x in script.reality_not_proven("dry"))
    assert not any("NOT a live proof" in x for x in script.reality_not_proven("live"))
    assert any("task id" in x for x in dry["not_real"])
    assert script.final_verdict("dry", True).startswith("DRY OK") and "NOT a live proof" in script.final_verdict("dry", True)
    assert script.final_verdict("live", True).startswith("PASS") and script.final_verdict("dry", False) == "NOT PASSED"
    assert "secret" not in json.dumps(script.reality_report("live", {"wire_posts": 1})).lower()


# ---------- checks (pure) ----------

def _wire(*phases_events):
    return [{"phase": p, "event": e} for p, e in phases_events]


def _good_snapshot(**over):
    intent = {"event": "intent", "key": "frame_001"}
    ledger = [{"api": "outsee", "key": "frame_001", "event": "intent", "status": "submit_unknown"}]
    snap = {
        "wire": _wire((1, "post_sent"), (1, "response_received")), "wire_posts": 1,
        "phase1_returncode": 137, "phase2_returncode": 0,
        "phase1": {"gate_refused_cap": 0, "refused_hosts": []},
        "phase2": {"step_outcome": "returned", "error_type": "OutseeIdempotencyUncertainError",
                   "error_code": "outsee_uncertain_submit", "new_post_attempted": False, "gate_refused_cap": 0,
                   "refused_hosts": [], "project_status": "generating_images", "project_images_ready": False,
                   "frame_status": "image_prompt_ready", "valid_image_on_disk": False},
        "records_after_phase1": {"idem_rows": [intent], "ledger_rows": ledger, "spend_rows": []},
        "records_after_phase2": {"idem_rows": [intent], "ledger_rows": ledger, "spend_rows": []},
        "files_after_phase1": [], "files_after_phase2": [], "fake_posts": 1, "fake_auth_failures": 0,
    }
    snap.update(over)
    return snap


def _failed(snap, mode="dry"):
    return set(script.verdict(script.evaluate(snap, mode=mode))[1])


def test_a_good_snapshot_passes_every_check_in_both_modes():
    checks = script.evaluate(_good_snapshot(), mode="dry")
    assert script.verdict(checks) == (True, []) and len({c["name"] for c in checks}) == len(checks) >= 12
    live = script.evaluate(_good_snapshot(fake_posts=None), mode="live")
    names = {c["name"] for c in live}
    assert script.verdict(live) == (True, []) and "caps_not_exceeded" in names and "provider_independently_saw_one_submit" not in names


def test_a_second_paid_post_after_the_restart_fails_the_money_checks():
    snap = _good_snapshot(wire=_wire((1, "post_sent"), (1, "response_received"), (2, "post_sent")), fake_posts=2)
    snap["phase2"] = {**snap["phase2"], "new_post_attempted": True, "error_code": None, "step_outcome": "returned",
                      "valid_image_on_disk": True, "project_images_ready": True, "frame_status": "image_generated"}
    assert {"exactly_one_paid_post_across_both_phases", "restart_sent_no_paid_request",
            "restart_outcome_is_a_safe_one_and_is_recorded", "provider_independently_saw_one_submit"} <= _failed(snap)


def test_a_refused_second_post_is_still_a_failure_because_production_tried():
    snap = _good_snapshot()
    snap["phase2"] = {**snap["phase2"], "new_post_attempted": True, "gate_refused_cap": 1}
    assert "restart_sent_no_paid_request" in _failed(snap)


def test_a_process_that_was_not_killed_or_never_posted_fails():
    assert "phase1_killed_by_a_hard_exit_after_the_paid_submit" in _failed(_good_snapshot(phase1_returncode=0))
    assert "phase1_killed_by_a_hard_exit_after_the_paid_submit" in _failed(_good_snapshot(wire=[], wire_posts=0))
    late = _good_snapshot(wire=_wire((1, "response_received"), (1, "post_sent")))
    assert "wire_counter_persisted_before_the_kill" in _failed(late)


def test_missing_or_extra_idempotency_rows_fail():
    two_intents = [{"event": "intent", "key": "frame_001"}, {"event": "intent", "key": "frame_001"}]
    snap = _good_snapshot(records_after_phase2={"idem_rows": two_intents, "ledger_rows": [], "spend_rows": []})
    assert {"restart_wrote_no_second_intent_and_no_result", "provider_ledger_has_one_open_intent_for_a_human"} <= _failed(snap)
    no_intent = _good_snapshot(records_after_phase1={"idem_rows": [], "ledger_rows": [], "spend_rows": []})
    assert "intent_on_disk_before_the_kill_and_no_verdict_after_it" in _failed(no_intent)


def test_a_restart_that_adds_a_charge_fails():
    row = {"provider": "outsee", "kind": "estimated", "usd": 0.3}
    snap = _good_snapshot(records_after_phase2={**_good_snapshot()["records_after_phase2"], "spend_rows": [row]})
    assert "restart_added_no_charge_and_no_spend_row" in _failed(snap)


def test_success_without_a_file_or_a_file_taken_by_the_killed_process_fails():
    snap = _good_snapshot()
    snap["phase2"] = {**snap["phase2"], "project_images_ready": True, "project_status": "images_ready"}
    assert "project_does_not_report_success_without_a_file" in _failed(snap)
    assert "nothing_was_taken_into_the_project_by_the_killed_process" in _failed(
        _good_snapshot(files_after_phase1=[{"name": "frame_001_x.png", "bytes": 5}]))


def test_a_foreign_request_or_a_dirty_restart_exit_fails():
    snap = _good_snapshot()
    snap["phase2"] = {**snap["phase2"], "refused_hosts": ["vibecode.moe"]}
    assert "no_foreign_request_in_either_phase" in _failed(snap)
    assert "restart_process_exited_cleanly" in _failed(_good_snapshot(phase2_returncode=1))


def test_the_restart_outcome_is_classified_from_the_childs_own_record():
    assert script.classify_phase2({}) == "no_record"
    assert script.classify_phase2({"error_code": "outsee_uncertain_submit"}) == "refused_unknown_outcome"
    assert script.classify_phase2({"error_code": "outsee_resume_only_no_result"}) == "refused_resume_only"
    assert script.classify_phase2({"adopted_recorded_result": True}) == "reused_recorded_result"
    assert script.classify_phase2({"new_post_attempted": True}) == "sent_second_request"
    assert script.classify_phase2({"step_outcome": "returned"}) == "other"


def test_the_missing_spend_row_of_the_killed_submit_is_reported_as_a_gap():
    notes = script.advisories(_good_snapshot())
    assert any(n.startswith("GAP") and "spend_ledger" in n for n in notes)
    with_row = _good_snapshot(records_after_phase1={**_good_snapshot()["records_after_phase1"],
                                                    "spend_rows": [{"provider": "outsee", "usd": 0.3}]})
    assert not any(n.startswith("GAP") for n in script.advisories(with_row))


# ---------- real dry runs: two child pythons, the first one hard-killed (nothing leaves the machine) ----------

def _run(tmp_path: Path, port: int, *extra: str) -> tuple[int, dict, str]:
    env = {**os.environ, "PYTHONUTF8": "1", "NO_COLOR": "1"}
    for name in ("GPT_RELAY_TOKEN", "GPT_PROXY_URL", "VIBECODE_API_KEY", "GPT_API_KEY", "OUTSEE_API_KEY", "KIE_API_KEY",
                 "ELEVENLABS_API_KEY"):
        env.pop(name, None)
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(_PATH), "--work-dir", str(tmp_path), "--dry", "--fake-port", str(port),
         "--port", str(port + 1), "--timeout-s", "240", *extra],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=560)
    result_path = tmp_path / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    return proc.returncode, result, (proc.stdout or "")[-2500:]


def test_a_real_dry_run_kills_after_the_submit_and_the_restart_pays_nothing(tmp_path):
    code, result, tail = _run(tmp_path, 18791)
    assert code == 0, tail
    summary = result["summary"]
    assert result["mode"] == "dry" and result["ok"] is True, result.get("failed")
    assert summary["verdict"].startswith("DRY OK") and "NOT a live proof" in summary["verdict"]
    assert result["phase1_returncode"] == 137 and result["phase2_returncode"] == 0
    assert result["wire_posts"] == 1 and result["fake_posts"] == 1
    assert [e["event"] for e in result["wire"]] == ["post_sent", "response_received"]
    # what the production code actually did on the restart (recorded, not assumed)
    assert summary["production_did_on_restart"] == "refused_unknown_outcome"
    assert result["phase2"]["error_code"] == "outsee_uncertain_submit" and result["phase2"]["new_post_attempted"] is False
    assert result["phase2"]["project_images_ready"] is False and result["phase2"]["valid_image_on_disk"] is False
    assert [r["event"] for r in result["records_after_phase1"]["idem_rows"]] == ["intent"]
    assert [r["event"] for r in result["records_after_phase2"]["idem_rows"]] == ["intent"]
    assert result["files_after_phase1"] == [] and result["files_after_phase2"] == []
    assert any(n.startswith("GAP") for n in summary["advisories"])
    assert (tmp_path / "wire.jsonl").is_file() and (tmp_path / "phase2.json").is_file()


def test_the_negative_control_makes_the_stands_own_production_code_pay_twice_and_is_not_passed(tmp_path):
    code, result, tail = _run(tmp_path, 18793, "--dry-scenario", "second_submit")
    assert code == 1, tail
    assert result["ok"] is False and result["summary"]["verdict"] == "NOT PASSED"
    assert result["wire_posts"] == 2 and result["fake_posts"] == 2
    assert {"exactly_one_paid_post_across_both_phases", "restart_sent_no_paid_request",
            "provider_independently_saw_one_submit"} <= set(result["failed"])
    assert result["summary"]["production_did_on_restart"] == "sent_second_request"
    assert result["phase1_returncode"] == 137  # the kill itself still worked: only the second POST makes it fail


def test_the_dry_run_refuses_a_work_dir_inside_live_data():
    proc = subprocess.run([sys.executable, "-X", "utf8", str(_PATH), "--work-dir", str(ROOT / "data" / "x"), "--dry"],
                          cwd=ROOT, env={**os.environ, "PYTHONUTF8": "1"}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode != 0 and not (ROOT / "data" / "x" / "result.json").exists()
