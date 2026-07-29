"""Багрепорт: окно логов + запись в баги/."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.bug_report import filter_log_window, write_bug_report


def test_filter_log_window_keeps_recent(tmp_path: Path) -> None:
    now = datetime(2026, 7, 29, 6, 30, 0, tzinfo=timezone.utc)
    lines = [
        "2026-07-29 06:00:00.000 | INFO | old",
        "2026-07-29 06:28:00.000 | INFO | recent",
        "  indented continuation",
        "2026-07-29 06:29:30.000 | ERROR | boom",
    ]
    got = filter_log_window("\n".join(lines), minutes=5, now=now)
    assert "old" not in got
    assert "recent" in got
    assert "boom" in got
    assert "indented continuation" in got


def test_write_bug_report_file(tmp_path: Path, monkeypatch) -> None:
    from app import settings as app_settings
    from app.services import bug_report as br

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    (tmp_path / "data").mkdir()
    log = tmp_path / "data" / "studio-live.log"
    log.write_text("2026-07-29 06:29:00 | INFO | hello\n", encoding="utf-8")

    bugs = tmp_path / "баги"
    bugs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(br, "bugs_dir", lambda: bugs)

    res = write_bug_report(description="сломался выбор промта", minutes=5)
    assert res["ok"] is True
    path = Path(res["path"])
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "сломался выбор промта" in text
    assert "studio-live.log" in text
    assert "clipboardPrompt" in res
    assert "баги/" in res["rel"]
