"""ai-pack: копии = SoT, START_HERE не врёт."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_ai_pack_verify_ok() -> None:
    build = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "build_ai_pack.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    verify = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "verify_ai_pack.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stdout + verify.stderr
    assert "VERIFY OK" in verify.stdout
