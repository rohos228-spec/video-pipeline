"""Read web/STUDIO_VERSION — UI build label + backend runtime fingerprint."""

from __future__ import annotations

import re
from pathlib import Path


def _version_file() -> Path:
    return Path(__file__).resolve().parents[2] / "web" / "STUDIO_VERSION"


def _parse_version_file() -> tuple[int, str, str, str]:
    path = _version_file()
    build = 0
    sha = "dev"
    attach_expected = ""
    orchestrator_expected = ""
    if path.is_file():
        # utf-8-sig: PowerShell Set-Content -Encoding UTF8 writes BOM; int("\ufeff160") fails -> v0
        lines = path.read_text(encoding="utf-8-sig").strip().splitlines()
        if lines:
            try:
                build = int(lines[0].strip().lstrip("\ufeff"))
            except ValueError:
                build = 0
        if len(lines) > 1 and lines[1].strip():
            sha = lines[1].strip()
        if len(lines) > 2 and lines[2].strip():
            attach_expected = lines[2].strip()
        if len(lines) > 3 and lines[3].strip():
            orchestrator_expected = lines[3].strip()
    return build, sha, attach_expected, orchestrator_expected


def _read_baked_ui_build() -> int:
    """Номер сборки из web/out/index.html (то, что видит браузер)."""
    idx = Path(__file__).resolve().parents[2] / "web" / "out" / "index.html"
    if not idx.is_file():
        return 0
    text = idx.read_text(encoding="utf-8", errors="ignore")
    for pat in (r'title="UI:\s*v(\d+)', r">v(\d+)\s*·", r'"v(\d+)\s*·'):
        m = re.search(pat, text)
        if m:
            return int(m.group(1))
    return 0


def _sqlite_project_count(db_path: Path) -> int:
    import sqlite3

    if not db_path.is_file():
        return 0
    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        try:
            row = conn.execute("SELECT COUNT(*) FROM projects").fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except Exception:
        return -1


def _running_backend_git_short() -> str:
    import subprocess

    root = Path(__file__).resolve().parents[2]
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def read_studio_version() -> dict[str, str | int | bool]:
    from app.settings import settings

    build, sha, attach_expected, orchestrator_expected = _parse_version_file()
    label = f"v{build} · {sha[:7]}" if sha and sha != "dev" else f"v{build}"
    db_path = settings.sqlite_path
    project_count = _sqlite_project_count(db_path)
    ui_baked_build = _read_baked_ui_build()
    ui_stale = ui_baked_build > 0 and ui_baked_build != build

    from app.bots.chatgpt import CHATGPT_ATTACH_LOGIC_ID
    from app.services.xlsx_step_runners import XLSX_STEP_RUNNERS_ID

    backend_attach = CHATGPT_ATTACH_LOGIC_ID
    backend_orchestrator = XLSX_STEP_RUNNERS_ID
    attach_ok = not attach_expected or attach_expected == backend_attach
    orchestrator_ok = (
        not orchestrator_expected or orchestrator_expected == backend_orchestrator
    )

    backend_git = _running_backend_git_short()
    return {
        "build": build,
        "sha": sha,
        "label": label,
        "backend_git": backend_git,
        "db_path": str(db_path),
        "project_count": project_count,
        "ui_baked_build": ui_baked_build,
        "ui_stale": ui_stale,
        "attach_expected": attach_expected,
        "backend_attach": backend_attach,
        "backend_ok": attach_ok,
        "orchestrator_expected": orchestrator_expected,
        "backend_orchestrator": backend_orchestrator,
        "orchestrator_ok": orchestrator_ok,
        "pipeline_ok": attach_ok and orchestrator_ok,
    }


def read_studio_version_label() -> str:
    return str(read_studio_version()["label"])
