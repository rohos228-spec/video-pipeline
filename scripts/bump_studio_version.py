#!/usr/bin/env python3
"""Increment web/STUDIO_VERSION before each UI-related commit/push."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "web" / "STUDIO_VERSION"


def git_short_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def bump() -> str:
    build = 0
    if VERSION_FILE.exists():
        first = VERSION_FILE.read_text(encoding="utf-8").strip().split("\n", 1)[0]
        try:
            build = int(first)
        except ValueError:
            build = 0
    build += 1
    sha = git_short_sha()
    attach = "paperclip-first-v69"
    orchestrator = "xlsx_step_runners-v70"
    try:
        text = (ROOT / "app" / "bots" / "chatgpt.py").read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith("CHATGPT_ATTACH_LOGIC_ID"):
                attach = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
        xsr = (ROOT / "app" / "services" / "xlsx_step_runners.py").read_text(
            encoding="utf-8"
        )
        for line in xsr.splitlines():
            if line.startswith("XLSX_STEP_RUNNERS_ID"):
                orchestrator = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    except Exception:
        pass
    VERSION_FILE.write_text(f"{build}\n{sha}\n{attach}\n{orchestrator}\n", encoding="utf-8")
    short = sha[:7] if sha != "unknown" else "dev"
    label = f"v{build} · {short}" if short != "dev" else f"v{build}"
    print(label)
    return label


if __name__ == "__main__":
    bump()
    sys.exit(0)
