import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
_PATH = ROOT / "scripts" / "e2e_elevenlabs_nodes.py"
_spec = importlib.util.spec_from_file_location("e2e_elevenlabs_nodes", _PATH)
script = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = script
_spec.loader.exec_module(script)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _stand_workdir import make_workdir, remove_workdir

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
TTS = "/v1/text-to-speech/pNInz6obpgDQGcFmaJgB"
SFX = "/v1/sound-generation"


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


def test_defaults_are_both_nodes_two_real_posts_and_five_cents():
    args = script._args(["--work-dir", "w", "--dry"])
    assert (args.node, args.max_real_posts, args.max_usd) == ("both", 2, 0.05)
    for node in ("sfx_gen", "audio", "both"):
        assert script._args(["--work-dir", "w", "--dry", "--node", node]).node == node
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--dry", "--node", "images"])


def test_live_needs_an_env_file():
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--live"])


def test_the_authorisation_covers_the_planned_real_posts_and_the_conservative_price():
    assert script.planned_real_posts("both") == 2 and script.planned_real_posts("audio") == 1
    assert script.authorisation_problem("both", 2, 0.05) is None
    assert "needs 2 real POST" in script.authorisation_problem("both", 1, 0.05)
    assert script.authorisation_problem("audio", 1, 0.05) is None and script.authorisation_problem("sfx_gen", 1, 0.05) is None
    assert "exceeds" in script.authorisation_problem("both", 2, 0.01)
    assert script.authorisation_problem("both", 0, 1) is not None and script.authorisation_problem("both", 2, 0) is not None
    for bad in (["--max-real-posts", "1"], ["--max-usd", "0.01"]):
        with pytest.raises(SystemExit):
            script._args(["--work-dir", "w", "--dry", "--node", "both", *bad])
    assert script.worst_case_usd("both") == pytest.approx(
        script.SFX_EVENT_SECONDS * script.USD_PER_SFX_SEC_HINT + script.TTS_MAX_CHARS * script.USD_PER_CHAR_HINT)


def test_the_price_hints_are_the_applications_own_constants():
    from app.services import media_spend
    assert script.USD_PER_CHAR_HINT == media_spend.ELEVENLABS_USD_PER_CHAR
    assert script.USD_PER_SFX_SEC_HINT == media_spend.ELEVENLABS_SFX_USD_PER_SEC


def test_the_live_phrases_fit_the_caps():
    assert len(script.TTS_TEXT) <= script.TTS_MAX_CHARS and len(script.TTS_TEXT_UNCERTAIN) <= script.TTS_MAX_CHARS
    assert script.SFX_EVENT_SECONDS <= script.SFX_MAX_SECONDS and script.SFX_PROMPT == "short soft click"
    assert script.worst_case_usd("both") < script.DEFAULT_MAX_USD
    assert script.app_cap_usd("both") > script.worst_case_usd("both")



def test_env_file_is_read_only_by_the_whitelist(tmp_path):
    env_file = tmp_path / "e.env"
    env_file.write_text("ELEVENLABS_API_KEY=secret-eleven\nELEVENLABS_PROXY_URL=http://127.0.0.1:3128\nKIE_API_KEY=kie-secret\n"
                        "OUTSEE_API_KEY=out-secret\nVIBECODE_API_KEY=vk\n# c\nGPT_PROXY_URL=http://x\n", encoding="utf-8")
    got = script.load_env_whitelist(str(env_file))
    assert set(got) == {"ELEVENLABS_API_KEY", "ELEVENLABS_PROXY_URL"} and got["ELEVENLABS_API_KEY"] == "secret-eleven"
    assert not any(k in got for k in ("KIE_API_KEY", "OUTSEE_API_KEY", "VIBECODE_API_KEY", "GPT_PROXY_URL"))


def test_the_report_never_contains_a_secret():
    text = json.dumps(script.reality_report("live", {"wire_posts": 2})) + json.dumps(script.reality_not_proven("live"))
    assert "secret" not in text.lower() and "2 real ElevenLabs POST" in text
    assert "real ElevenLabs POST" not in json.dumps(script.reality_report("dry", {"wire_posts": 2}))


def test_live_refuses_before_starting_anything_without_a_key_or_with_a_bad_proxy(tmp_path, capsys):
    no_key = tmp_path / "nokey.env"
    no_key.write_text("KIE_API_KEY=x\n", encoding="utf-8")
    assert asyncio.run(script.main(["--work-dir", str(tmp_path / "w"), "--live", "--env-file", str(no_key)])) == 3
    bad_proxy = tmp_path / "badproxy.env"
    bad_proxy.write_text("ELEVENLABS_API_KEY=abcdefgh\nELEVENLABS_PROXY_URL=ftp://example.org\n", encoding="utf-8")
    assert asyncio.run(script.main(["--work-dir", str(tmp_path / "w"), "--live", "--env-file", str(bad_proxy)])) == 3
    out = capsys.readouterr().out
    assert "nothing sent" in out and "abcdefgh" not in out
    assert not (tmp_path / "w").exists()



def _gate(**kw):
    return script.Gate(mode=kw.pop("mode", "dry"), **kw)


def _tts(gate, text=script.TTS_TEXT, **kw):
    return gate.judge(kw.get("method", "POST"), kw.get("scheme", "https"), kw.get("host", script.ELEVEN_HOST),
                      kw.get("path", TTS), {"text": text})


def _sfx(gate, seconds=1.0, prompt=script.SFX_PROMPT):
    return gate.judge("POST", "https", script.ELEVEN_HOST, SFX, {"text": prompt, "duration_seconds": seconds})


def test_only_the_two_elevenlabs_posts_are_let_through_everything_else_is_refused():
    gate = _gate()
    for kw in ({"host": "example.com"}, {"host": "api.elevenlabs.io.evil.com"}, {"host": None}, {"scheme": "http"},
               {"method": "GET"}, {"path": "/v1/voices"}, {"path": "/v1/text-to-speech/a/stream"},
               {"path": "/v1/user/subscription"}, {"host": "127.0.0.1"}):
        assert _tts(gate, **kw).action == "refuse", kw
    assert gate.wire == [] and len(gate.refused) == 9
    assert _tts(gate).kind == "tts" and _sfx(_gate()).kind == "sfx"


def test_one_real_post_per_kind_and_a_total_cap():
    gate = _gate(max_real_posts=2)
    assert _sfx(gate).action == "pass" and _tts(gate).action == "pass"
    assert gate.count("sfx") == 1 and gate.count("tts") == 1
    assert _sfx(gate).action == "refuse" and _tts(gate).action == "refuse"
    assert [r["why"] for r in gate.refused] == ["paid_cap", "paid_cap"]
    one = _gate(max_real_posts=2)
    assert _tts(one).action == "pass" and _tts(one).action == "refuse" and one.refused[0]["why"] == "kind_cap"
    capped = _gate(max_real_posts=1)
    assert _sfx(capped).action == "pass" and _tts(capped).action == "refuse" and capped.refused[0]["why"] == "paid_cap"


def test_the_live_size_caps_apply_to_the_text_and_the_sound():
    gate = _gate()
    assert _tts(gate, "я" * (script.TTS_MAX_CHARS + 1)).action == "refuse"
    assert _sfx(gate, 1.5).action == "refuse" and _sfx(gate, 22).action == "refuse"
    assert gate.judge("POST", "https", script.ELEVEN_HOST, SFX, {"text": "x", "duration_seconds": "n/a"}).action == "refuse"
    assert _sfx(gate, prompt="p" * (script.SFX_MAX_PROMPT_CHARS + 1)).action == "refuse"
    assert gate.wire == [] and {r["why"] for r in gate.refused} == {"tts_too_long", "sfx_too_long"}
    assert _tts(gate, "я" * script.TTS_MAX_CHARS).action == "pass" and _sfx(gate, 1.0).action == "pass"


def test_a_simulated_loss_is_delivered_instead_of_the_request_and_never_counts_as_a_wire_post():
    gate = _gate(max_real_posts=1)
    gate.arm("kill")
    judged = _sfx(gate)
    assert (judged.action, judged.fault) == ("fault", "kill") and gate.wire == [] and gate.fault is None
    assert gate.attempts("sfx") == 1 and gate.attempts("tts") == 0 and gate.total_attempts == 1
    assert _sfx(gate).action == "pass"
    gate.arm("timeout")
    assert _tts(gate).fault == "timeout" and gate.attempts("tts") == 1 and gate.count("tts") == 0
    gate.arm("timeout")
    assert gate.judge("GET", "https", "example.com", "/", {}).action == "refuse" and gate.fault == "timeout"



@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")
def test_the_fake_audio_passes_the_applications_real_verification(tmp_path):
    from app.services.audio_verification import verify_audio
    for seconds in (1.0, 1.5):
        path = tmp_path / f"x{seconds}.mp3"
        path.write_bytes(script.make_audio(seconds))
        verdict = verify_audio(path, expect_duration=seconds, duration_tol=0.6)
        assert verdict.ok, verdict.problems
    assert len(script.make_audio(1.0)) >= 1000


def test_environment_is_scratch_with_a_fake_key_and_no_proxy(tmp_path):
    env = script.env_overrides(tmp_path, cap=0.12, dry=True)
    assert env["ELEVENLABS_API_KEY"] == script.FAKE_KEY and env["ELEVENLABS_PROXY_URL"] == ""
    assert env["DATA_DIR"] == str(tmp_path) and Path(env["SQLITE_PATH"]).parent == tmp_path
    assert env["MEDIA_SPEND_RECORDING"] == "true" and env["KIE_API_KEY"] == "" and env["OUTSEE_API_KEY"] == ""
    assert {env["SPEND_DAILY_CAP_USD"], env["SPEND_PROJECT_CAP_USD"], env["SPEND_RUN_CAP_USD"]} == {"0.12"}
    assert float(env["DISK_GUARD_LOW_FREE_GB"]) < 0.01
    assert "DISK_GUARD_LOW_FREE_GB" not in script.env_overrides(tmp_path, cap=0.12, dry=False)


def test_the_verdict_never_claims_a_live_proof_for_a_dry_run():
    assert script.final_verdict("dry", True).startswith("DRY OK") and "NOT a live proof" in script.final_verdict("dry", True)
    assert script.final_verdict("live", True).startswith("PASS") and script.final_verdict("dry", False) == "NOT PASSED"
    assert any("NOT a live proof" in x for x in script.reality_not_proven("dry"))
    assert not any("NOT a live proof" in x for x in script.reality_not_proven("live"))
    assert any("no request-key sidecar" in x for x in script.reality_not_proven("live"))
    assert script.verdict([script.check("a", True), script.check("b", False)]) == (False, ["b"])



def test_sfx_progress_checkpoint_reaches_the_database_on_every_save(tmp_path, monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.models import Base, Project, ProjectStatus
    from app.services import sfx_gen
    from app.services.ai_result_io import ai_job_checkpoint
    from app.settings import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")

    async def go():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[Project.__table__]))
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as session:
            project = Project(slug="sfx-progress", topic="t", status=ProjectStatus.new, auto_mode=False, meta={})
            session.add(project)
            await session.commit()
            pid = project.id
            prog = {"events": {}, "inflight": {}}
            prog["inflight"]["0"] = {"kind": "hit"}
            await sfx_gen._save_progress(session, project, prog)
            prog["events"]["0"] = {"idx": 0, "provider": "elevenlabs", "path": "x"}
            prog["inflight"].pop("0")
            await sfx_gen._save_progress(session, project, prog)
            prog["inflight"]["1"] = {"kind": "hit"}
            await sfx_gen._save_progress(session, project, prog)
        async with sessions() as fresh:
            stored = ai_job_checkpoint(await fresh.get(Project, pid), "sfx_progress")
        await engine.dispose()
        return stored

    stored = asyncio.run(go())
    assert list(stored["events"]) == ["0"] and stored["events"]["0"]["provider"] == "elevenlabs"
    assert list(stored["inflight"]) == ["1"]



def _run(tmp_path: Path, *extra: str) -> tuple[int, dict, str]:
    env = {**os.environ, "PYTHONUTF8": "1", "NO_COLOR": "1"}
    for name in ("ELEVENLABS_API_KEY", "ELEVENLABS_PROXY_URL", "KIE_API_KEY", "OUTSEE_API_KEY", "VIBECODE_API_KEY"):
        env.pop(name, None)
    proc = subprocess.run([sys.executable, "-X", "utf8", str(_PATH), "--work-dir", str(tmp_path), "--dry", *extra],
                          cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=560)
    result_path = tmp_path / "result.json"
    result = json.loads(result_path.read_text(encoding="utf-8")) if result_path.exists() else {}
    return proc.returncode, result, (proc.stdout or "")[-1800:]


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")
def test_a_real_dry_run_proves_both_nodes_offline(tmp_path):
    code, result, tail = _run(tmp_path, "--node", "both")
    assert code == 0, tail
    assert result["mode"] == "dry" and result["ok"] is True, result.get("failed")
    summary, counters = result["summary"], result["counters"]
    assert summary["verdict"].startswith("DRY OK") and "NOT a live proof" in summary["verdict"]
    assert (counters["wire_sfx"], counters["wire_tts"], counters["wire_posts"], counters["real_posts"]) == (1, 1, 2, 0)
    assert counters["simulated_losses"] == 2 and counters["refused"] == 0
    names = {c["name"] for c in result["checks"]}
    assert len(names) == len(result["checks"]) >= 25
    assert {"sfx_rerun_in_resume_only_finishes_without_post", "sfx_uncertain_event_is_not_resent_and_is_marked",
            "tts_rerun_is_answered_from_the_sidecar_without_a_post", "tts_marker_blocks_the_automatic_resend",
            "tts_lost_answer_recovered_under_resume_only_without_a_post"} <= names
    rows = result["spend_rows"]
    assert any(r["note"].startswith("sfx dur=") for r in rows) and any(r["note"].startswith("tts ") for r in rows)
    assert all(r["provider"] == "elevenlabs" and r["project_id"] and r["run_id"] and r["request_id"] for r in rows)
    assert "fake" in json.dumps(result["reality"]["not_real"]) and "real ElevenLabs" not in json.dumps(result["reality"]["real"])


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")
def test_a_dry_run_of_one_node_runs_only_that_node(tmp_path):
    code, result, tail = _run(tmp_path, "--node", "audio")
    assert code == 0, tail
    assert result["counters"]["wire_sfx"] == 0 and result["counters"]["wire_tts"] == 1
    assert not any(c["name"].startswith("sfx_") for c in result["checks"])


@pytest.mark.slow
def test_the_dry_run_refuses_a_work_dir_inside_live_data():
    proc = subprocess.run([sys.executable, "-X", "utf8", str(_PATH), "--work-dir", str(ROOT / "data" / "x"), "--dry"],
                          cwd=ROOT, env={**os.environ, "PYTHONUTF8": "1"}, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode != 0 and not (ROOT / "data" / "x" / "result.json").exists()
