from __future__ import annotations

from pathlib import Path

from app.services import disk_guard, spend_guard

SECRET = "sk-ABCDEFGH1234567890zzQQ"


def test_append_rotating_redacts_secrets(tmp_path: Path) -> None:
    log = tmp_path / "errors.log"
    disk_guard.append_rotating(log, f"call failed Authorization: Bearer {SECRET} token={SECRET}")
    text = log.read_text(encoding="utf-8")
    assert SECRET not in text
    assert "call failed" in text


def test_spend_note_cleaner_redacts_and_truncates() -> None:
    cleaned = spend_guard._clean(f"create kie task=1 key={SECRET} " + "x" * 400)
    assert SECRET not in cleaned and len(cleaned) <= 200
    assert spend_guard._clean(None) == ""
