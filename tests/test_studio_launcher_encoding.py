"""Regression: Windows PowerShell 5.1 requires UTF-8 BOM for Cyrillic in .ps1 files."""

from __future__ import annotations

import re
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


def test_run_backend_respects_env_web_host() -> None:
    root = Path(__file__).resolve().parents[1]
    text = (root / "scripts/run-backend.ps1").read_text(encoding="utf-8-sig")
    assert "VpWebBind.ps1" in text
    assert '$env:WEB_HOST = "127.0.0.1"' not in text
    assert "Ensure-VpFleetNetworkEnv" in text


def test_vp_web_bind_and_fleet_fix_scripts_exist() -> None:
    root = Path(__file__).resolve().parents[1]
    assert (root / "scripts/VpWebBind.ps1").is_file()
    assert (root / "scripts/fleet-network-fix.ps1").is_file()
    assert (root / "FLEET-NETWORK-FIX.cmd").is_file()
