"""Тесты чистой части стенда F08 ``scripts/e2e_action_layer_local_exec.py`` (сервер и ffmpeg здесь не запускаются)."""
from __future__ import annotations

import importlib.util
import re
import sqlite3
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "e2e_action_layer_local_exec.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("e2e_action_layer_local_exec_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_scrub_env_drops_secret_names(mod):
    env = {"PATH": "x", "KIE_API_KEY": "1", "TELEGRAM_BOT_TOKEN": "2", "SOME_SECRET": "3", "DB_PASSWORD": "4",
           "gpt_api_key": "5", "HOME": "h"}
    assert mod.scrub_env(env) == {"PATH": "x", "HOME": "h"}


def test_server_env_is_loopback_local_and_keyless(mod, outside_tmp):
    tmp_path = outside_tmp
    env = mod.server_env({"PATH": "p", "VIBECODE_API_KEY": "k", "FLEET_AGENT_TOKEN": "t"}, tmp_path / "w", 18999, "medium")
    assert env["WEB_HOST"] == "127.0.0.1" and env["WEB_PORT"] == "18999"
    assert env["TELEGRAM_ENABLED"] == "false" and env["WORKER_ENABLED"] == "false"
    assert env["HF_HUB_OFFLINE"] == "1" and env["ASR_BACKEND"] == "whisper" and env["WHISPER_MODEL"] == "medium"
    assert Path(env["SQLITE_PATH"]).parent == tmp_path / "w" / "data" and Path(env["DATA_DIR"]) == tmp_path / "w" / "data"
    assert not mod.inside_repo(Path(env["SQLITE_PATH"]))
    assert Path(env["AL_STAND_PROMPTS"]) == tmp_path / "w" / "prompts"
    assert "VIBECODE_API_KEY" not in env and "FLEET_AGENT_TOKEN" not in env


def test_inside_repo(mod):
    assert mod.inside_repo(mod.ROOT / "data") and mod.inside_repo(mod.ROOT)
    assert not mod.inside_repo(Path("C:/video-pipeline/_f08_work/x"))


def test_main_requires_run_flag(mod):
    with pytest.raises(SystemExit) as ei:
        mod.main([])
    assert ei.value.code == 2


def test_main_refuses_work_dir_inside_repo(mod, capsys):
    code = mod.main(["--run", "--work-dir", str(mod.ROOT / "data" / "f08_should_not_exist")])
    assert code == 2
    assert "ВНЕ репозитория" in capsys.readouterr().err
    assert not (mod.ROOT / "data" / "f08_should_not_exist").exists()


def test_main_refuses_non_empty_work_dir(mod, outside_tmp, capsys):
    tmp_path = outside_tmp
    (tmp_path / "old.txt").write_text("x")
    assert mod.main(["--run", "--work-dir", str(tmp_path)]) == 2
    assert "не пуст" in capsys.readouterr().err


def test_report_known_defects_do_not_fail_unless_strict(mod):
    r = mod.Report()
    assert r.check("a", True) is True
    assert r.check("b", False, "why", known="D-9") is True      # известный дефект: стенд не падает
    assert r.failed == [] and r.known_defects == {"D-9": ["b"]}
    assert mod.final_verdict(r).startswith(mod.VERDICT_OK) and "D-9" in mod.final_verdict(r)
    assert r.check("c", False, "real failure") is False
    assert r.failed == ["c"] and mod.final_verdict(r) == mod.VERDICT_FAIL
    strict = mod.Report(strict=True)
    strict.check("b", False, known="D-9")
    assert strict.failed == ["b"] and mod.final_verdict(strict) == mod.VERDICT_FAIL


def test_verdict_label_wording(mod):
    assert mod.VERDICT_OK == "LOCAL OK (live server process, no provider involved)"
    assert mod.final_verdict(mod.Report()) == mod.VERDICT_OK


def test_loopback_detection(mod):
    assert mod.is_loopback_host("127.0.0.1") and mod.is_loopback_host("localhost") and mod.is_loopback_host("::1")
    assert not mod.is_loopback_host("api.kie.ai") and not mod.is_loopback_host("8.8.8.8")


def test_tree_digest_and_sha(mod, tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "f.md").write_text("one")
    d1 = mod.tree_digest(tmp_path)
    assert d1 == mod.tree_digest(tmp_path)
    (tmp_path / "a" / "f.md").write_text("two")
    assert mod.tree_digest(tmp_path) != d1
    assert mod.sha256_file(tmp_path / "nope") is None and len(mod.sha256_file(tmp_path / "a" / "f.md")) == 64


def test_db_helpers_and_money_digest(mod, tmp_path):
    path = tmp_path / "s.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE spend_ledger (id INTEGER PRIMARY KEY, usd REAL)")
    con.commit()
    con.close()
    db = mod.Db(path)
    snap0 = mod.money_snapshot(db)
    assert snap0["spend_ledger"]["rows"] == 0 and snap0["provider_task_ledger"]["exists"] is False
    assert db.write("INSERT INTO spend_ledger (usd) VALUES (?)", (0.5,)) == 1
    assert mod.money_snapshot(db) != snap0 and mod.money_snapshot(db)["spend_ledger"]["rows"] == 1
    assert db.has_table("spend_ledger") and not db.has_table("zzz")


def test_cached_model_lookup(mod, tmp_path):
    snap = tmp_path / "hub" / "models--Systran--faster-whisper-tiny" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    assert mod.find_cached_model("tiny", {"HF_HUB_CACHE": str(tmp_path / "hub")}) is None
    (snap / "model.bin").write_bytes(b"x")
    assert mod.find_cached_model("tiny", {"HF_HUB_CACHE": str(tmp_path / "hub")}) is not None
    assert mod.find_cached_model("large-v9", {"HF_HUB_CACHE": str(tmp_path / "hub")}) is None


def test_reality_lists_state_what_is_not_proven(mod):
    lists = mod.reality_lists()
    assert set(lists) == {"real", "not_real", "not_proven"} and all(lists.values())
    assert any("платные" in x and "F09" in x for x in lists["not_proven"])
    assert any("NeMo" in x for x in lists["not_proven"])
    assert any("SAPI" in x for x in lists["not_real"])


def test_skipped_actions_mark_paid(mod):
    catalog = {"a.paid": {"risk": "paid", "spends_money": True}, "a.local": {"risk": "local_safe", "spends_money": False},
               "a.done": {"risk": "read", "spends_money": False}}
    got = {x["action_id"]: x for x in mod.skipped_actions(catalog, {"a.done"})}
    assert set(got) == {"a.paid", "a.local"} and "ПЛАТНОЕ" in got["a.paid"]["reason"]


def test_has_russian(mod):
    assert mod.has_russian("План создан") and not mod.has_russian("plan created")


def test_validate_wav(mod, tmp_path):
    import wave
    good = tmp_path / "g.wav"
    with wave.open(str(good), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 8000 * 4)
    assert mod.validate_wav(good)["seconds"] == 4.0
    short = tmp_path / "s.wav"
    with wave.open(str(short), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 800)
    with pytest.raises(ValueError):
        mod.validate_wav(short)


def test_script_source_stays_free_and_local():
    src = SCRIPT.read_text(encoding="utf-8")
    assert not re.search(r"post\([^)]*from-text", src), "платный планировщик /from-text не вызывается"
    assert "dotenv" not in src.lower() and "open('.env'" not in src and '".env"' not in src
    for banned in ("git add", "git commit", "git stash", "git checkout"):
        assert banned not in src
    assert "http://127.0.0.1" in src or "127.0.0.1" in src
    assert not re.search(r"https?://(?!127\.0\.0\.1|localhost)", src.replace("http://evil", ""))


def test_scenario_actions_exist_in_real_catalog_with_expected_tiers(mod):
    """Стенд опирается на список действий и уровней; если каталог разошёлся - тест красный раньше живого прогона."""
    from app.services.action_layer.spec import ensure_builtin_actions, registry
    ensure_builtin_actions()
    expected = {
        "montage.align_audio": "local_safe", "montage.run_montage": "destructive", "node.reset_from_step": "destructive",
        "montage.set_voiceover": "local_safe", "montage.set_coverage": "local_safe", "shot.edit_field": "local_safe",
        "node.set_config": "local_safe", "project.set_auto_mode": "local_safe", "prompt.save_master": "destructive",
        "montage.queue_ops": "local_safe", "montage.cancel_queue_ops": "local_safe", "history.list_snapshots": "read",
        "history.rollback": "destructive",
    }
    for aid, tier in expected.items():
        spec = registry.find(aid)
        assert spec is not None, aid
        assert spec.risk == tier and spec.spends_money is False, aid
        assert spec.needs_approval == (tier == "destructive"), aid
    # все D11-обёртки, которые трогает стенд, отдают снимок
    from app.services.action_layer.builtin_wave5_history import HANDLERS
    for aid in ("montage.set_voiceover", "montage.set_coverage", "shot.edit_field", "prompt.save_master"):
        assert aid in HANDLERS
