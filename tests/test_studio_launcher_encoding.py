"""Regression: Windows PowerShell 5.1 requires UTF-8 BOM for Cyrillic in .ps1 files."""

from __future__ import annotations

from pathlib import Path

UTF8_BOM = b"\xef\xbb\xbf"

LAUNCHER_SCRIPTS = (
    "scripts/studio.ps1",
    "scripts/run-backend.ps1",
    "scripts/stop-backend.ps1",
)


def test_launcher_ps1_files_have_utf8_bom() -> None:
    root = Path(__file__).resolve().parents[1]
    for rel in LAUNCHER_SCRIPTS:
        data = (root / rel).read_bytes()
        assert data.startswith(UTF8_BOM), f"{rel} must start with UTF-8 BOM for Windows PS 5.1"


def test_studio_ps1_parses_as_utf8_text() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "scripts/studio.ps1").read_text(encoding="utf-8-sig")
    assert "function Start-StudioChromeCdp" in text
    assert "function Invoke-StudioDoctor" in text
    assert text.count("{") == text.count("}")
