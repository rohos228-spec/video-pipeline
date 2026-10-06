import importlib.util
import json
import sys
from pathlib import Path

_p = Path(__file__).parent.parent / "scripts" / "live_media_probe.py"
_s = importlib.util.spec_from_file_location("live_media_probe", _p)
probe = importlib.util.module_from_spec(_s)
sys.modules["live_media_probe"] = probe
_s.loader.exec_module(probe)

SENTINELS = {
    "ELEVENLABS_API_KEY": "sk-eleven-SENTINEL-1111",
    "ELEVENLABS_PROXY_URL": "http://user:pw-SENTINEL-2222@proxy.invalid:1",
    "ELEVENLABS_PROXY_ALT_URL": "http://user:pw-SENTINEL-3333@alt.invalid:1",
    "AUDIO_USE_ELEVENLABS_FALLBACK": "fallback-SENTINEL-4444",
    "OUTSEE_API_KEY": "osk-SENTINEL-5555",
    "OUTSEE_API_BASE_URL": "https://base-SENTINEL-6666.invalid",
    "OUTSEE_DEFAULT_IMAGE_MODEL": "model-SENTINEL-7777",
}


def _env_file(tmp_path, extra=""):
    f = tmp_path / "e.env"
    f.write_text("\n".join(f"{k}={v}" for k, v in SENTINELS.items()) + "\n" + extra, encoding="utf-8")
    return str(f)


class Fake:
    """Fake app seams: counts calls, never touches network, DB or the app."""

    def __init__(self, tmp_path, leak=False, resume_ok=True):
        self.calls = []
        self.actual = 0
        self.spent = 0.0
        self.leak = leak
        self.resume_ok = resume_ok
        self.tmp = tmp_path
        self.guard_calls = []

    def runner(self, step, usd, submits=1, size=10):
        async def run(work):
            self.calls.append(step)
            self.actual += submits
            self.spent += usd
            out = self.tmp / f"{step}.bin"
            out.write_bytes(b"x" * size)
            res = {"output": out, "second_call_refused": self.resume_ok}
            if self.leak:
                res["error"] = "boom " + SENTINELS["ELEVENLABS_API_KEY"]
            return res
        return run

    async def snapshot(self):
        return {"actual_rows": self.actual, "task_events": 0, "spent_usd": self.spent}

    async def guard(self, est):
        self.guard_calls.append(est)
        return {"allowed": True, "code": ""}

    def deps(self, usd=0.01, submits=1):
        return probe.Deps(
            prepare=lambda env, work: None,
            runners={s: self.runner(s, usd, submits) for s in probe.STEPS},
            snapshot=self.snapshot, guard=self.guard, work=self.tmp, cleanup=False)


def _rows(capsys):
    out = capsys.readouterr().out
    return out, [json.loads(line) for line in out.splitlines() if line.startswith("{")]


def test_refuses_without_live(tmp_path, capsys):
    fake = Fake(tmp_path)
    assert probe.main(["--env-file", _env_file(tmp_path), "--steps", "tts"], deps=fake.deps()) == 2
    assert "needs --live" in capsys.readouterr().out
    assert fake.calls == []


def test_refuses_unknown_step_and_empty_steps(tmp_path, capsys):
    fake = Fake(tmp_path)
    env = _env_file(tmp_path)
    assert probe.main(["--live", "--env-file", env, "--steps", "tts,music"], deps=fake.deps()) == 2
    assert "unknown step" in capsys.readouterr().out
    assert probe.main(["--live", "--env-file", env], deps=fake.deps()) == 2  # default: no steps
    assert fake.calls == []


def test_whitelist_is_exact(tmp_path):
    assert set(probe._ENV) == set(SENTINELS)
    assert len(probe._ENV) == len(SENTINELS)
    extra = "GPT_API_KEY=nope\nKIE_API_KEY=nope\nYANDEX_STORAGE_SECRET_KEY=nope\n# ELEVENLABS_API_KEY=commented\n"
    loaded = probe.load_env(_env_file(tmp_path, extra))
    assert loaded == SENTINELS


def test_never_prints_env_values(tmp_path, capsys):
    fake = Fake(tmp_path, leak=True)
    out_json = tmp_path / "rows.json"
    rc = probe.main(["--live", "--env-file", _env_file(tmp_path), "--steps", "tts,sfx,image", "--out", str(out_json)],
                    deps=fake.deps())
    out, rows = _rows(capsys)
    assert rc == 1  # the fake reports an error
    assert rows and fake.calls == ["tts", "sfx", "image"]
    for value in SENTINELS.values():
        assert value not in out
        assert value not in out_json.read_text(encoding="utf-8")
    assert "***" in out  # the leaked key inside the error text was scrubbed


def test_happy_path_row_fields(tmp_path, capsys):
    fake = Fake(tmp_path)
    rc = probe.main(["--live", "--env-file", _env_file(tmp_path), "--steps", "tts"], deps=fake.deps(usd=0.036))
    _out, rows = _rows(capsys)
    assert rc == 0
    row = rows[0]
    assert row["step"] == "tts" and row["ok"] is True
    assert row["submits"] == 1 and row["output_exists"] is True and row["output_bytes"] == 10
    assert row["second_call_refused"] is True and row["guard_spend_usd"] == 0.036
    assert fake.guard_calls == [probe.estimate_usd("tts")]


def test_total_usd_cap_enforced_before_step(tmp_path, capsys):
    fake = Fake(tmp_path)
    # tts est 0.036 + sfx est 0.006 fit into 0.05; image est 0.10 does not
    rc = probe.main(["--live", "--env-file", _env_file(tmp_path), "--steps", "tts,sfx,image", "--total-usd", "0.05"],
                    deps=fake.deps(usd=0.0))
    _out, rows = _rows(capsys)
    assert fake.calls == ["tts", "sfx"]
    image = [r for r in rows if r.get("step") == "image"][0]
    assert image["refused"] is True and "--total-usd" in image["error"]
    assert rc == 1


def test_max_submits_cap_enforced_before_step(tmp_path, capsys):
    fake = Fake(tmp_path)
    probe.main(["--live", "--env-file", _env_file(tmp_path), "--steps", "tts,sfx,image", "--max-submits", "2"],
               deps=fake.deps(usd=0.0))
    _out, rows = _rows(capsys)
    assert fake.calls == ["tts", "sfx"]
    assert [r for r in rows if r.get("step") == "image"][0]["error"].startswith("--max-submits")


def test_extra_recorded_submit_halts_run(tmp_path, capsys):
    fake = Fake(tmp_path)
    rc = probe.main(["--live", "--env-file", _env_file(tmp_path), "--steps", "tts,sfx"],
                    deps=fake.deps(usd=0.0, submits=2))
    _out, rows = _rows(capsys)
    assert fake.calls == ["tts"]  # a double submit stops everything after it
    assert rows[0]["violations"] and rows[1]["refused"] is True
    assert rc == 1


def test_resume_only_not_proven_is_a_violation(tmp_path, capsys):
    fake = Fake(tmp_path, resume_ok=False)
    rc = probe.main(["--live", "--env-file", _env_file(tmp_path), "--steps", "sfx"], deps=fake.deps())
    _out, rows = _rows(capsys)
    assert rc == 1 and rows[0]["violations"]


def test_missing_key_skips_step_without_calling(tmp_path, capsys):
    f = tmp_path / "e.env"
    f.write_text("ELEVENLABS_API_KEY=" + SENTINELS["ELEVENLABS_API_KEY"] + "\n", encoding="utf-8")
    fake = Fake(tmp_path)
    probe.main(["--live", "--env-file", str(f), "--steps", "image"], deps=fake.deps())
    _out, rows = _rows(capsys)
    assert fake.calls == [] and rows[0]["skipped"] is True


def test_tts_text_within_limit_and_step_caps():
    assert len(probe.TTS_TEXT) <= 120
    assert probe.STEP_CAP_USD == {"tts": 0.10, "sfx": 0.05, "image": 0.10}
    assert probe.DEFAULT_MAX_SUBMITS == 3 and probe.DEFAULT_TOTAL_USD == 0.25
    assert sum(probe.estimate_usd(s) for s in probe.STEPS) <= probe.DEFAULT_TOTAL_USD
