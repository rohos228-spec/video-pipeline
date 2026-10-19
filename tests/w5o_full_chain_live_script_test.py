from __future__ import annotations

import importlib.util
import json
import socket
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "e2e_full_chain_live.py"

SECRET_A = "kie" + "_" + "SECRETVALUE1234567890"
SECRET_B = "vb" + "_" + "SECRETVALUE0987654321"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("e2e_full_chain_live_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _env_file(tmp_path, lines):
    p = tmp_path / "keys.env"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _full_env(tmp_path, extra=()):
    return _env_file(tmp_path, [f"VIBECODE_API_KEY={SECRET_B}", f"OUTSEE_API_KEY=outsee{SECRET_A}", f"KIE_API_KEY={SECRET_A}",
                                f"ELEVENLABS_API_KEY=eleven{SECRET_A}", "UNRELATED_TOKEN=must-not-be-read-at-all", *extra])



def test_arguments_defaults_and_modes(mod):
    a = mod.parse_args(["--live", "--work-dir", "x"])
    assert a.live and not a.live_preflight and a.until == "E" and a.frames == 1
    assert (a.max_text_usd, a.max_media_usd) == (2.0, 1.0) and a.with_music is False and a.with_sfx is False
    assert (a.text_provider, a.image_provider, a.video_provider) == ("vibecode", "outsee", "outsee")
    p = mod.parse_args(["--live-preflight", "--until", "c"])
    assert p.live_preflight and p.until == "C" and p.work_dir is None


@pytest.mark.parametrize("argv", [
    [],
    ["--live", "--live-preflight", "--work-dir", "x"],
    ["--live"],
    ["--live", "--work-dir", "x", "--frames", "0"],
    ["--live", "--work-dir", "x", "--frames", "6"],
    ["--live", "--work-dir", "x", "--max-text-usd", "2.5"],
    ["--live", "--work-dir", "x", "--max-media-usd", "1.01"],
    ["--live", "--work-dir", "x", "--max-media-usd", "0"],
    ["--live", "--work-dir", "x", "--video-provider", "seedance"],
])
def test_bad_arguments_exit(mod, argv):
    with pytest.raises(SystemExit):
        mod.parse_args(argv)


def test_bad_level_is_a_refusal_not_a_crash(mod):
    assert mod.main(["--live-preflight", "--until", "Z"]) == mod.EXIT_ARGS



def test_loader_keeps_only_whitelisted_names_and_prints_nothing(mod, tmp_path, capsys):
    got = mod.load_env_whitelist(str(_full_env(tmp_path, ["# KIE_API_KEY=commented", 'GPT_PROXY_URL="http://p"'])))
    assert set(got) <= set(mod.ENV_NAMES) and "UNRELATED_TOKEN" not in got
    assert got["KIE_API_KEY"] == SECRET_A and got["GPT_PROXY_URL"] == "http://p"
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_env_file_can_come_from_the_environment_variable(mod, monkeypatch, tmp_path):
    monkeypatch.setenv(mod.ENV_FILE_VAR, str(tmp_path / "x.env"))
    assert mod.parse_args(["--live-preflight"]).env_file == str(tmp_path / "x.env")


def test_script_never_opens_a_repository_dotenv(mod):
    src = SCRIPT.read_text(encoding="utf-8")
    assert "\".env\"" not in src and "'.env'" not in src
    assert "WORKER_USE_PROJECT_DB" in src


def test_preflight_output_has_no_secret_values(mod, tmp_path, capsys):
    f = _full_env(tmp_path)
    code = mod.main(["--live-preflight", "--until", "C", "--env-file", str(f)])
    out = capsys.readouterr()
    assert code == mod.EXIT_PASS
    assert SECRET_A not in out.out + out.err and SECRET_B not in out.out + out.err
    assert "оценка:" in out.out and "потолк" in out.out



def test_refuses_without_env_file_and_without_keys(mod, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(mod.ENV_FILE_VAR, raising=False)
    assert mod.main(["--live-preflight"]) == mod.EXIT_ARGS
    assert "файл ключей" in capsys.readouterr().err
    only = _env_file(tmp_path, [f"VIBECODE_API_KEY={SECRET_B}"])
    code = mod.main(["--live", "--until", "E", "--work-dir", str(tmp_path / "w"), "--env-file", str(only)])
    cap = capsys.readouterr()
    assert code == mod.EXIT_ARGS and "нет ключей" in cap.err
    for name in ("OUTSEE_API_KEY", "ELEVENLABS_API_KEY"):
        assert name in cap.err
    assert SECRET_B not in cap.out + cap.err
    assert not (tmp_path / "w").exists()


def test_required_keys_follow_level_and_providers(mod):
    a = mod.parse_args(["--live-preflight", "--until", "A"])
    assert set(mod.required_keys(a)) == {"VIBECODE_API_KEY"}
    c = mod.parse_args(["--live-preflight", "--until", "C", "--image-provider", "kie"])
    assert set(mod.required_keys(c)) == {"VIBECODE_API_KEY", "KIE_API_KEY", "OUTSEE_API_KEY"}
    e = mod.parse_args(["--live-preflight", "--until", "E", "--with-music"])
    assert set(mod.required_keys(e)) == {"VIBECODE_API_KEY", "OUTSEE_API_KEY", "ELEVENLABS_API_KEY", "KIE_API_KEY"}
    assert mod.missing_keys({"OUTSEE_API_KEY": "short", "VIBECODE_API_KEY": "fake-llm-key-123"}, mod.required_keys(e)) == sorted(mod.required_keys(e))


def test_default_estimate_fits_the_ceilings_and_music_does_not(mod, tmp_path, capsys):
    a = mod.parse_args(["--live-preflight", "--until", "E"])
    est = mod.estimate(a)
    assert est["requests"]["music"] == 0 and est["requests"]["sfx"] == 0
    assert est["requests"]["llm"] == 10 and "music_prompt" not in est["llm_by_node"] and "sfx_plan" not in est["llm_by_node"]
    assert mod.estimate_problem(est, 2.0, 1.0) is None
    m = mod.parse_args(["--live-preflight", "--until", "E", "--with-music", "--with-sfx"])
    assert mod.estimate_problem(mod.estimate(m), 2.0, 1.0)
    f = _full_env(tmp_path)
    assert mod.main(["--live-preflight", "--until", "E", "--with-music", "--with-sfx", "--env-file", str(f)]) == mod.EXIT_ARGS
    assert "выше потолка" in capsys.readouterr().err
    assert mod.main(["--live-preflight", "--until", "C", "--max-media-usd", "0.1", "--env-file", str(f)]) == mod.EXIT_ARGS


def test_estimate_counts_per_frame_and_levels(mod):
    one = mod.estimate(mod.parse_args(["--live-preflight", "--until", "C", "--frames", "1"]))
    three = mod.estimate(mod.parse_args(["--live-preflight", "--until", "C", "--frames", "3"]))
    assert one["requests"]["image"] == 1 and three["requests"]["image"] == 3 and three["requests"]["video"] == 3
    assert three["text_usd"] > one["text_usd"] and three["media_usd"] > one["media_usd"]
    a = mod.estimate(mod.parse_args(["--live-preflight", "--until", "A"]))
    assert a["requests"]["image"] == a["requests"]["video"] == a["requests"]["tts"] == 0 and a["media_usd"] == 0


def test_wire_caps_have_slack_but_no_music_or_sfx_unless_asked(mod):
    a = mod.parse_args(["--live-preflight", "--until", "E"])
    c = mod.wire_caps(a)
    assert c["outsee_image"] == 3 and c["outsee_video"] == 2 and c["kie_music"] == 0 and c["sfx"] == 0 and c["kie_media"] == 0
    b = mod.parse_args(["--live-preflight", "--until", "E", "--with-music", "--with-sfx", "--video-provider", "kie"])
    cb = mod.wire_caps(b)
    assert cb["kie_music"] == 1 and cb["sfx"] == 9 and cb["kie_media"] == 2 and cb["outsee_video"] == 0



def test_preflight_makes_no_network_calls(mod, tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used in preflight")
    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket.socket, "connect_ex", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", boom)
    f = _full_env(tmp_path)
    assert mod.main(["--live-preflight", "--until", "E", "--env-file", str(f)]) == mod.EXIT_PASS


def test_url_problems(mod):
    assert mod.url_problem("X", "https://api.kie.ai") is None
    assert mod.url_problem("X", "http://api.kie.ai")
    assert mod.url_problem("X", "https://127.0.0.1:9")
    assert mod.url_problem("X", "https://u:p@host.example")



ALLOWED = ["vibecode.moe", "api.kie.ai", "outsee.io", "api.elevenlabs.io", "catbox.moe"]
CAPS = {"llm": 2, "outsee_image": 1, "outsee_video": 1, "tts": 1, "sfx": 0, "kie_music": 0, "kie_media": 1}


def _judge(mod, method, host, path, body=None, counts=None, scheme="https", caps=CAPS):
    return mod.judge_request(method, host, scheme, path, body, counts if counts is not None else {}, caps, ALLOWED)


def test_hook_counts_paid_posts_and_refuses_over_the_cap(mod):
    counts: dict = {}
    assert _judge(mod, "POST", "outsee.io", "/api/v1/images/generate", counts=counts)[:2] == ("pass", "outsee_image")
    act, bucket, why = _judge(mod, "POST", "outsee.io", "/api/v1/images/generate", counts=counts)
    assert (act, bucket, why) == ("refuse", "outsee_image", "cap_exceeded") and counts == {"outsee_image": 1}
    assert _judge(mod, "POST", "api.kie.ai", "/api/v1/generate", counts=counts)[2] == "cap_exceeded"
    assert _judge(mod, "POST", "api.elevenlabs.io", "/v1/sound-generation", counts=counts)[2] == "cap_exceeded"
    assert _judge(mod, "POST", "api.elevenlabs.io", "/v1/text-to-speech/abc123", counts=counts)[0] == "pass"
    assert _judge(mod, "POST", "vibecode.moe", "/v1/chat/completions", counts=counts)[1] == "llm"


def test_hook_refuses_foreign_hosts_unknown_posts_and_plain_http(mod):
    assert _judge(mod, "POST", "evil.example", "/v1/chat/completions")[2] == "host_not_allowed"
    assert _judge(mod, "POST", "outsee.io", "/api/v1/whatever")[2] == "unknown_post"
    assert _judge(mod, "POST", "outsee.io", "/api/v1/images/generate", scheme="http")[2] == "not_https"
    assert _judge(mod, "PUT", "outsee.io", "/x")[2] == "method_not_allowed"
    assert _judge(mod, "DELETE", "outsee.io", "/x")[2] == "method_not_allowed"
    assert _judge(mod, "GET", "cdn.example", "/clip.mp4")[0] == "pass"
    assert _judge(mod, "GET", "cdn.example", "/clip.mp4", scheme="http")[2] == "http_get_non_loopback"
    assert _judge(mod, "POST", "127.0.0.1", "/anything")[0] == "pass"


def test_hook_free_uploads_pass_without_a_cap(mod):
    assert _judge(mod, "POST", "catbox.moe", "/user/api.php")[:2] == ("pass", "upload")
    assert _judge(mod, "POST", "api.kie.ai", "/api/file-stream-upload")[:2] == ("pass", "upload")


def test_hook_blocks_seedance_2_5_but_not_1_5(mod):
    bad = json.dumps({"model": "bytedance/seedance-2-5", "input": {}}).encode()
    bad2 = json.dumps({"model": "seedance_2.5"}).encode()
    ok = json.dumps({"model": "bytedance/seedance-1.5-pro"}).encode()
    for body in (bad, bad2):
        assert _judge(mod, "POST", "api.kie.ai", "/api/v1/jobs/createTask", body)[2] == "forbidden_model"
    counts: dict = {}
    assert _judge(mod, "POST", "api.kie.ai", "/api/v1/jobs/createTask", ok, counts)[:2] == ("pass", "kie_media")
    assert not mod.FORBIDDEN_MODEL_RE.search("seedance-1-5-pro") and not mod.FORBIDDEN_MODEL_RE.search("veo-3-1-lite")



def _cfg(mod, **kw):
    base = {"text_provider": "vibecode", "image_provider": "outsee", "video_provider": "outsee", "with_sfx": False,
            "app_cap_usd": 10.0}
    return {**base, **kw}


def test_settings_plan_blanks_unneeded_routes_and_disables_publish(mod):
    env = {"VIBECODE_API_KEY": SECRET_B, "OUTSEE_API_KEY": "o" + SECRET_A}
    plan = mod.live_settings_plan(_cfg(mod), env)
    assert plan["vibecode_api_key"] == SECRET_B and plan["gpt_api_key"] == SECRET_B
    assert plan["kie_api_key"] == "" and plan["elevenlabs_api_key"] == "" and plan["gpt_relay_token"] == ""
    assert plan["social_publish_enabled"] is False and plan["sfx_enabled"] is False
    assert plan["browser_cdp_url"].startswith("http://127.0.0.1:") and plan["gpt_base_url"] == "https://vibecode.moe"
    assert plan["spend_run_cap_usd"] == 10.0
    kie = mod.live_settings_plan(_cfg(mod, text_provider="kie"), {"KIE_API_KEY": SECRET_A})
    assert kie["vibecode_api_key"] == "" and kie["gpt_api_key"] == SECRET_A and kie["text_llm_provider"] == "kie"



def test_ledger_spend_splits_text_and_media_and_counts_unknown_rows(mod):
    rows = [
        {"kind": "reserve", "provider": "vibecode", "usd": 0.05, "note": "llm"},
        {"kind": "settle", "provider": "vibecode", "usd": -0.04, "note": "basis=reported"},
        {"kind": "actual", "provider": "outsee", "usd": 0.0, "note": "cost_unknown image"},
        {"kind": "actual", "provider": "elevenlabs", "usd": 0.04, "note": "tts"},
        {"kind": "event", "provider": "outsee", "usd": 0.0, "note": "x"},
        {"kind": "actual", "provider": "kie", "usd": 0.0, "note": "gpt chat cost_unknown"},
    ]
    s = mod.ledger_spend(rows)
    assert s["text_usd"] == pytest.approx(0.01 + 0.05) and s["media_usd"] == pytest.approx(0.30 + 0.04)
    assert s["unknown_rows"] == 2 and s["paid_rows"] == 5
    assert mod.ceiling_breach(s, 2.0, 1.0) == ""
    assert mod.ceiling_breach({"text_usd": 2.01, "media_usd": 0}, 2.0, 1.0).startswith("СТОП-ПОТОЛОК: текст")
    assert mod.ceiling_breach({"text_usd": 0, "media_usd": 1.2}, 2.0, 1.0).startswith("СТОП-ПОТОЛОК: медиа")


def test_artifact_problems(mod, tmp_path):
    img, vid = tmp_path / "a.png", tmp_path / "v.mp4"
    img.write_bytes(b"x" * 10)
    vid.write_bytes(b"")
    arts = [{"kind": "scene_image", "path": str(img)}, {"kind": "scene_video", "path": str(vid)},
            {"kind": "audio", "path": str(tmp_path / "gone.mp3")}]
    probs = mod.artifact_problems(arts, "D")
    assert any("пустой файл" in p for p in probs) and any("файла нет" in p for p in probs)
    ok = mod.artifact_problems([{"kind": "scene_image", "path": str(img)}], "B")
    assert ok == []
    assert mod.artifact_problems([], "B") == ["нет артефакта scene_image для уровня B"]


def test_dead_api_keeps_mutations_and_answers_nothing(mod):
    d = mod.DeadApi([{"method": "PATCH"}])
    assert d.get("/x") == (0, None) and d.post("/y") == (0, None) and d.mutations == [{"method": "PATCH"}]


def test_silent_mp3_is_a_real_nonempty_file(mod):
    try:
        data = mod.make_silent_mp3(2)
    except Exception as exc:
        pytest.skip(f"ffmpeg not available: {exc}")
    assert len(data) > 1000


def test_dry_script_delegates_live_calls(mod):
    src = (ROOT / "scripts" / "e2e_full_chain.py").read_text(encoding="utf-8")
    assert "e2e_full_chain_live" in src and "--live-preflight" in src
