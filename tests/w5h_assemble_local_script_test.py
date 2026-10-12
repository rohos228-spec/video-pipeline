"""Guards for scripts/e2e_assemble_local.py (the local node `assemble`): pure helpers, the lavfi media makers, the checks and a
real --dry run. Everything is local and free: there is no --live mode and no provider; the dry run starts the whole
application in a SUBPROCESS (isolated memory and ports), so run this file alone: one pytest at a time.
"""
import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_PATH = ROOT / "scripts" / "e2e_assemble_local.py"
_spec = importlib.util.spec_from_file_location("e2e_assemble_local", _PATH)
script = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = script
_spec.loader.exec_module(script)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stand_workdir import make_workdir, remove_workdir  # noqa: E402

PORT = 18809
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")


@pytest.fixture
def tmp_path():
    """Outside the repository: the e2e guard refuses a work folder inside live data."""
    path = make_workdir()
    try:
        yield path
    finally:
        remove_workdir(path)


# ---------- arguments ----------

def test_dry_is_the_only_mode_and_a_work_dir_is_required():
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w"])
    with pytest.raises(SystemExit):
        script._args(["--dry"])
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--dry", "--live"])  # there is no live mode: nothing paid exists on this node
    assert script._args(["--work-dir", "w", "--dry"]).port == script.PORT


def test_the_stand_refuses_to_start_when_the_application_settings_are_already_loaded(tmp_path, capsys):
    import app.settings  # noqa: F401  (this very process has imported the application)
    assert asyncio.run(script.main(["--work-dir", str(tmp_path / "w"), "--dry"])) == 3
    assert "nothing was started" in capsys.readouterr().out and not (tmp_path / "w").exists()


def test_the_node_is_the_assemble_node_with_its_native_step_code_and_admitted_for_a_local_rerun():
    from app.orchestrator.node_registry import WORK_NODES
    from app.services import local_node_recovery, media_admission, node_capabilities  # noqa: F401  (registers the specs)
    assert WORK_NODES[script.NODE].step_code == script.STEP_CODE
    assert local_node_recovery.spec_ok(script.NODE) and media_admission.local_worker(script.NODE)
    assert tuple(script.REQUIRED_TICKET_CHECKS) == ("artifact_row", "file_inside_project", "claimed_at_known",
                                                    "produced_after_claim", "verify_artifact_passed",
                                                    "digest_matches_artifact", "approve_final_pending",
                                                    "journal_proof_matches", "ledger_unchanged", "no_paid_seam_hit",
                                                    "no_model_requests", "no_part_files")


def test_the_forced_failure_looks_like_ffmpeg_to_the_node_so_the_node_calls_it_repeatable():
    from app.orchestrator.steps import assemble as asm
    assert asm._assemble_retryable(RuntimeError(script.FAULT))
    assert not asm._assemble_retryable(RuntimeError("нет кадров с видео-клипами"))  # an input error is not repeatable


# ---------- the lavfi media ----------

@needs_ffmpeg
def test_the_made_clip_and_voice_decode_and_have_the_expected_length(tmp_path):
    clip, voice = tmp_path / "clip.mp4", tmp_path / "frame_001.mp3"
    script.make_clip(clip, "red")
    script.make_voice(voice, 440)
    from app.services.artifact_verifier import verify_artifact
    got_clip = asyncio.run(verify_artifact(clip, root=tmp_path, kind="video"))
    got_voice = asyncio.run(verify_artifact(voice, root=tmp_path, kind="audio"))
    assert got_clip["status"] == "passed" and (got_clip["width"], got_clip["height"]) == (160, 284)
    assert abs(got_clip["duration"] - script.CLIP_SECONDS) < 0.2
    assert got_voice["status"] == "passed" and abs(got_voice["duration"] - script.FRAME_SECONDS) < 0.2


@needs_ffmpeg
def test_the_full_voice_is_the_concatenation_of_the_frame_voices(tmp_path):
    parts = []
    for i in (1, 2, 3):
        p = tmp_path / f"frame_{i:03d}.mp3"
        script.make_voice(p, 300 + i * 100)
        parts.append(p)
    full = tmp_path / "voice_full.mp3"
    script.concat_voice(parts, full)
    from app.services.media_probe import probe_duration
    total = asyncio.run(probe_duration(full))
    assert 4.3 <= total <= 4.9 and not (tmp_path / "voice_parts.txt").exists()


def test_the_asr_probe_touches_no_network_and_loads_no_model():
    got = script.asr_probe()
    assert isinstance(got["faster_whisper_installed"], bool) and isinstance(got["cached_whisper_models"], list)
    assert got["used_by_this_stand"] is False and "per-frame" in got["why"]


# ---------- checks ----------

def _good_snapshot(**over):
    ticket = {"status": "verified", "incident_id": "inc-1",
              "preconditions": {"action_type": "retry_same", "media_operation": "retry_local_worker"},
              "verification_result": {"verified": True, "problems": [], "result_confirmed": True,
                                      **{k: True for k in script.REQUIRED_TICKET_CHECKS},
                                      "local_details": {"paid_seam_hits": 0}}}
    blocked = {"refused": True, "tickets_after": 0}
    snap = {
        "counters": {"injected_failures": 18, "unexpected_model": 0, "refused_foreign": 0},
        "probes": {"kill_switch_recovery": blocked},
        "diagnoses": {"incidents": [{"node_key": script.TARGET, "type": "media_failure", "media": True,
                                     "incident_id": "inc-1", "status": "resolved_confirmed"}]},
        "decisions": {"records": [{"status": "done", "model_steps": 2}]},
        "actions": {"recoveries": [ticket]},
        "decision_contexts": [{"media_recovery": {"operation": "retry_local_worker", "submits_new_paid_request": False}}],
        "node_status": {script.TARGET: "done"}, "node_attempts": 10, "step_failure": {"abandoned_at": "t"},
        "state_at_exhaustion": {"node": "failed", "final_files": [], "approvals": 0, "part_files": []},
        "final_check": {"status": "passed", "streams": ["video", "audio"], "duration": 4.5, "sha256": "abc"},
        "approvals": [{"kind": "approve_final", "decision": "pending", "sha256": "abc"}],
        "part_files": [], "ledger_rows": [], "spend_rows_paid": [], "refused_hosts": [],
        "asr_probe": {"faster_whisper_installed": False, "cached_whisper_models": []},
    }
    snap.update(over)
    return snap


def _failed(snap):
    return set(script.verdict(script.evaluate(snap))[1])


def test_a_good_snapshot_passes_every_check():
    checks = script.evaluate(_good_snapshot())
    assert script.verdict(checks) == (True, [])
    assert len({c["name"] for c in checks}) == len(checks) >= 17


def test_a_failure_phase_that_published_something_fails():
    snap = _good_snapshot(state_at_exhaustion={"node": "failed", "final_files": ["x.mp4"], "approvals": 1, "part_files": []})
    assert "nothing_was_published_by_the_failed_phase" in _failed(snap)


def test_native_retries_that_never_ran_out_fail():
    snap = _good_snapshot(step_failure={}, node_attempts=1, counters={"injected_failures": 2, "unexpected_model": 0,
                                                                       "refused_foreign": 0})
    assert {"failure_injected_and_native_retries_ran", "native_retries_exhausted_and_project_paused"} <= _failed(snap)


def test_a_second_ticket_or_an_unverified_one_fails():
    ticket = _good_snapshot()["actions"]["recoveries"][0]
    assert "exactly_one_ticket_and_it_is_verified" in _failed(_good_snapshot(actions={"recoveries": [ticket, ticket]}))
    assert "exactly_one_ticket_and_it_is_verified" in _failed(_good_snapshot(actions={"recoveries": []}))
    weak = {**ticket, "verification_result": {**ticket["verification_result"], "approve_final_pending": False}}
    assert "ticket_checks_all_true_from_independent_facts" in _failed(_good_snapshot(actions={"recoveries": [weak]}))


def test_a_recovery_that_hit_a_paid_seam_fails():
    ticket = _good_snapshot()["actions"]["recoveries"][0]
    bad = {**ticket, "verification_result": {**ticket["verification_result"], "no_paid_seam_hit": False,
                                             "local_details": {"paid_seam_hits": 1}}}
    assert "recovery_attempt_ran_with_zero_paid_seam_hits" in _failed(_good_snapshot(actions={"recoveries": [bad]}))


def test_the_approval_must_stay_with_the_human():
    assert "approve_final_for_this_file_is_pending_and_unapproved" in _failed(_good_snapshot(
        approvals=[{"kind": "approve_final", "decision": "approved", "sha256": "abc"}]))
    assert "approve_final_for_this_file_is_pending_and_unapproved" in _failed(_good_snapshot(approvals=[]))
    two = [{"kind": "approve_final", "decision": "pending", "sha256": "abc"}] * 2
    assert "approve_final_for_this_file_is_pending_and_unapproved" in _failed(_good_snapshot(approvals=two))


def test_a_final_that_the_real_verifier_does_not_pass_or_without_audio_fails():
    assert "final_file_passes_the_real_verifier_after_the_run" in _failed(_good_snapshot(final_check={"status": "failed", "reason": "x"}))
    assert "final_file_passes_the_real_verifier_after_the_run" in _failed(_good_snapshot(
        final_check={"status": "passed", "streams": ["video"], "duration": 4.5, "sha256": "abc"}))
    assert "final_file_passes_the_real_verifier_after_the_run" in _failed(_good_snapshot(
        final_check={"status": "passed", "streams": ["video", "audio"], "duration": 1.0, "sha256": "abc"}))


def test_a_decision_that_does_not_see_the_local_rerun_or_a_denied_kill_switch_is_named():
    assert "decision_sees_the_local_worker_rerun_as_the_only_offer" in _failed(_good_snapshot(
        decision_contexts=[{"media_recovery": {"operation": None, "denied": "source_proof_missing"}}]))
    assert "kill_switch_stops_the_recovery" in _failed(_good_snapshot(
        probes={"kill_switch_recovery": {"refused": False, "tickets_after": 1}}))


def test_any_provider_row_or_foreign_request_fails():
    assert "no_provider_ledger_and_no_spend" in _failed(_good_snapshot(ledger_rows=[{"api": "outsee"}]))
    assert "no_provider_ledger_and_no_spend" in _failed(_good_snapshot(spend_rows_paid=[{"provider": "outsee"}]))
    assert "nothing_left_the_machine" in _failed(_good_snapshot(refused_hosts=["outsee.io"]))


def test_the_verdict_and_the_reality_report_say_what_was_not_exercised():
    assert script.final_verdict(True).startswith("DRY OK") and "ASR/subtitles NOT exercised" in script.final_verdict(True)
    assert script.final_verdict(False) == "NOT PASSED"
    reality = script.reality_report()
    text = json.dumps(reality, ensure_ascii=False)
    assert "ASR / Whisper" in text and "Telegram: off" in text and "forced failure" in text and "Decision model: local stub" in text
    assert any("subtitles" in x for x in script.reality_not_proven())


# ---------- a real dry run (whole app in a subprocess, nothing leaves the machine) ----------

def _run(tmp_path: Path) -> tuple[int, dict, str]:
    env = {**os.environ, "PYTHONUTF8": "1", "NO_COLOR": "1"}
    for name in ("GPT_RELAY_TOKEN", "GPT_PROXY_URL", "VIBECODE_API_KEY", "GPT_API_KEY", "OUTSEE_API_KEY", "KIE_API_KEY",
                 "ELEVENLABS_API_KEY"):
        env.pop(name, None)
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(_PATH), "--work-dir", str(tmp_path), "--dry", "--port", str(PORT),
         "--settle-s", "3", "--sleep-minutes", "0.01", "--timeout-s", "420"],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=560)
    result_path = tmp_path / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    return proc.returncode, result, (proc.stdout or "")[-1800:]


@needs_ffmpeg
def test_a_real_dry_run_reassembles_once_under_the_ticket_and_leaves_the_approval_to_the_human(tmp_path):
    code, result, tail = _run(tmp_path)
    assert code == 0, tail
    summary, counters = result["summary"], result["counters"]
    assert result["ok"] is True, result.get("failed")
    assert summary["verdict"].startswith("DRY OK") and "NOT exercised" in summary["verdict"]
    assert counters["injected_failures"] >= 4 and counters["injected_failures"] % 2 == 0  # one in-node retry per attempt
    assert result["node_attempts"] >= 9 and result["state_at_exhaustion"]["final_files"] == []
    recoveries = result["actions"]["recoveries"]
    assert len(recoveries) == 1 and recoveries[0]["status"] == "verified"
    ver = recoveries[0]["verification_result"]
    assert ver["result_confirmed"] is True and all(ver[k] is True for k in script.REQUIRED_TICKET_CHECKS)
    assert ver["local_details"]["paid_seam_hits"] == 0 and ver["local_details"]["approve_final"]["approved_by_supervisor"] is False
    final = result["final_check"]
    assert final["status"] == "passed" and {"video", "audio"} <= set(final["streams"]) and 3.5 <= final["duration"] <= 6.0
    assert [a["decision"] for a in result["approvals"]] == ["pending"]
    assert result["ledger_rows"] == [] and result["spend_rows_paid"] == [] and result["refused_hosts"] == []
    assert result["probes"]["kill_switch_recovery"]["refused"] is True
    assert isinstance(result["asr_probe"]["faster_whisper_installed"], bool)
    assert any("ASR" in x for x in result["reality"]["not_real"])


@pytest.mark.slow
def test_the_dry_run_refuses_a_work_dir_inside_live_data():
    proc = subprocess.run([sys.executable, "-X", "utf8", str(_PATH), "--work-dir", str(ROOT / "data" / "x"), "--dry"],
                          cwd=ROOT, env={**os.environ, "PYTHONUTF8": "1"}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode != 0 and not (ROOT / "data" / "x" / "result.json").exists()
