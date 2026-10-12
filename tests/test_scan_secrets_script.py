"""Сканер секретов (A16): находит посаженные секреты, не печатает значения, пропускает чистые файлы и медиа."""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "scan_secrets", Path(__file__).resolve().parent.parent / "scripts" / "scan_secrets.py")
scan = importlib.util.module_from_spec(_SPEC)
sys.modules["scan_secrets"] = scan
_SPEC.loader.exec_module(scan)  # type: ignore[union-attr]

BOT = "123456789:" + "A" * 35
GOOGLE = "AIza" + "B" * 35
SB = "sb_secret_" + "C" * 20


def test_planted_secrets_are_found_in_text_and_sqlite(tmp_path: Path) -> None:
    (tmp_path / "log.txt").write_text(f"x {BOT} y {GOOGLE}\nsupabase {SB}\n", encoding="utf-8")
    db = tmp_path / "x.db"
    con = sqlite3.connect(db)
    con.execute("create table t(note text)")
    con.execute("insert into t values (?)", (f"Bearer {'D' * 30}",))
    con.commit()
    con.close()
    report = scan.scan_tree([tmp_path])
    found = {k for h in report["hits"] for k in h["found"]}
    assert {"telegram_bot_token", "google_api_key", "supabase_key", "bearer_token"} <= found
    assert report["total_matches"] >= 4


def test_clean_and_redacted_text_has_no_matches(tmp_path: Path) -> None:
    (tmp_path / "log.txt").write_text(
        "Authorization: Bearer [redacted]\napi_key: [redacted]\nпроект 12 потрачено 0.03\n", encoding="utf-8")
    assert scan.scan_tree([tmp_path])["total_matches"] == 0


def test_media_files_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "clip.mp4").write_bytes(BOT.encode())
    report = scan.scan_tree([tmp_path])
    assert report["files_scanned"] == 0 and report["total_matches"] == 0


def test_process_env_value_is_found_but_never_printed(tmp_path: Path, monkeypatch, capsys) -> None:
    secret = "zz-very-private-value-123456"
    monkeypatch.setenv("MY_TEST_API_KEY", secret)
    (tmp_path / "a.txt").write_text(f"leaked {secret}", encoding="utf-8")
    code = scan.main([str(tmp_path), "--include-process-env"])
    out = capsys.readouterr().out
    assert code == 1
    assert "env:MY_TEST_API_KEY=1" in out
    assert secret not in out
