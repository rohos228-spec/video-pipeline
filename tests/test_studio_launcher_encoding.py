"""Regression: Windows PowerShell 5.1 requires UTF-8 BOM for Cyrillic in .ps1 files."""

from __future__ import annotations

import re
from pathlib import Path

UTF8_BOM = b"\xef\xbb\xbf"
# UTF-8 em-dash / en-dash. Byte 0x94 of em-dash is '"' in CP1251, which
# closes a double-quoted string and then `[4]` looks like a type literal.
EM_DASH_UTF8 = "\u2014".encode("utf-8")
EN_DASH_UTF8 = "\u2013".encode("utf-8")
ELLIPSIS_UTF8 = "\u2026".encode("utf-8")

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


def test_launcher_ps1_files_use_crlf() -> None:
    root = Path(__file__).resolve().parents[1]
    for rel in LAUNCHER_SCRIPTS:
        data = (root / rel).read_bytes()
        assert b"\r\n" in data, f"{rel} must use CRLF for Windows PS 5.1"
        assert data.replace(b"\r\n", b"").count(b"\n") == 0, f"{rel} has bare LF"


def test_launcher_ps1_no_unicode_dashes() -> None:
    """PS 5.1 without BOM treats the file as CP1251: em-dash bytes become a quote."""
    root = Path(__file__).resolve().parents[1]
    for rel in (*LAUNCHER_SCRIPTS, "STUDIO.cmd"):
        data = (root / rel).read_bytes()
        assert EM_DASH_UTF8 not in data, f"{rel} contains U+2014 em-dash (breaks PS 5.1 strings)"
        assert EN_DASH_UTF8 not in data, f"{rel} contains U+2013 en-dash"
        assert ELLIPSIS_UTF8 not in data, f"{rel} contains U+2026 ellipsis"


def test_preflight_python_uses_here_string() -> None:
    """`; from` inside a double-quoted python -c is a PS parser error if the string breaks."""
    root = Path(__file__).resolve().parents[1]
    for rel in ("scripts/studio.ps1", "scripts/run-backend.ps1"):
        text = (root / rel).read_text(encoding="utf-8-sig")
        assert "$preflightPy = @'" in text, f"{rel} must pass create_app preflight as a here-string"
        assert "from app.web.api import create_app" in text
        assert not re.search(
            r'-c\s+"[^"]*from\s+app\.web',
            text,
        ), f"{rel} still has python -c \"... from ...\" (PS 5.1 'from' keyword trap)"


def test_studio_ps1_menu_and_profile() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "scripts/studio.ps1").read_text(encoding="utf-8-sig")
    assert "VP_REPO_ROOT" in text
    assert "VpBrowserProfile.ps1" in text
    assert "function Invoke-StudioStop" in text
    assert "function Invoke-StudioBrowserAi" in text
    assert "function Invoke-StudioDoctor" in text
    assert text.count("{") == text.count("}")
    assert re.search(r'ValidateSet\("1", "2", "3", "4", "5", "6"', text)
    for item in ("[1]", "[2]", "[3]", "[4]", "[5]", "[6]"):
        assert item in text


def test_stop_backend_supports_wait_sec() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "scripts/stop-backend.ps1").read_text(encoding="utf-8-sig")
    assert "WaitSec" in text
    assert "VP_REPO_ROOT" in text
