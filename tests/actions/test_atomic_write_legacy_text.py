"""Старый текст и HTML мигрируют с версии 0. Чужая кодировка — не allowlist."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.orchestrator.node_store.atomic_write import VersionConflict, atomic_write_text
from app.settings import settings


def test_plain_text_legacy_override_migrates_from_zero(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    dest = tmp_path / "node_overrides" / "note.txt"
    dest.parent.mkdir(parents=True)
    raw = "просто текст оверрайда"
    dest.write_text(raw, encoding="utf-8")
    atomic_write_text(dest, "wrapped", expected_version=0)
    backup = dest.parent / "old" / f"{dest.name}.v0"
    assert backup.read_text(encoding="utf-8") == raw
    body = json.loads(dest.read_text(encoding="utf-8"))
    assert body == {"version": 1, "text": "wrapped"}


def test_legacy_html_report_migrates_from_zero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    dest = tmp_path / "reports" / "page.html"
    dest.parent.mkdir(parents=True)
    html = "<html><p>отчёт</p></html>"
    dest.write_text(html, encoding="utf-8")
    nxt = "<html><p>next</p></html>"
    atomic_write_text(dest, nxt, expected_version=0)
    backup = dest.parent / "old" / f"{dest.name}.v0"
    assert backup.read_text(encoding="utf-8") == html
    body = dest.read_text(encoding="utf-8")
    assert body == nxt
    assert not body.lstrip().startswith("{")
    side = dest.with_name(dest.name + ".version")
    assert side.read_text(encoding="utf-8").strip() == "1"
    atomic_write_text(dest, "<html><p>two</p></html>", expected_version=1)
    assert dest.read_text(encoding="utf-8") == "<html><p>two</p></html>"
    assert side.read_text(encoding="utf-8").strip() == "2"


def test_non_utf8_legacy_is_conflict_not_allowlist_error(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    dest = tmp_path / "node_overrides" / "cp.txt"
    dest.parent.mkdir(parents=True)
    raw = "привет".encode("cp1251")
    dest.write_bytes(raw)
    with pytest.raises(VersionConflict) as exc:
        atomic_write_text(dest, "x", expected_version=0)
    assert "allowlist" not in str(exc.value).lower()
    assert not isinstance(exc.value, ValueError) or type(exc.value) is VersionConflict
    assert dest.read_bytes() == raw
