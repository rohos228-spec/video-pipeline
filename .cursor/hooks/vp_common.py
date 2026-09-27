"""Общие функции хуков video-pipeline. Только стандартная библиотека Python."""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PROTECTED_BRANCHES = {"main", "master"}

SECRET_NAME = re.compile(r"^(\.env(\..+)?|.*\.pem|.*\.key|Data_video_pipeline\.env|credentials.*\.json)$", re.I)
SECRET_OK = {".env.example", ".env.fleet.example"}


def project_root() -> Path:
    env = os.environ.get("CURSOR_PROJECT_DIR") or os.environ.get("CLAUDE_PROJECT_DIR")
    return Path(env).resolve() if env else Path.cwd().resolve()


def state_dir() -> Path:
    d = project_root() / ".cursor" / "hooks" / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log(event: str, message: str) -> None:
    try:
        with (state_dir() / "hooks.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{event}] {message}\n")
    except OSError:
        pass


def read_input() -> dict:
    raw = sys.stdin.buffer.read().decode("utf-8", "replace")
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


def respond(payload: dict) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def is_secret_path(path: str) -> bool:
    p = Path(path.replace("\\", "/"))
    if p.name in SECRET_OK:
        return False
    if "browser_profile" in p.parts:
        return True
    return bool(SECRET_NAME.match(p.name))


def git(cwd: Path | str, *args: str, timeout: float = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    out = proc.stdout.decode("utf-8", "replace") or proc.stderr.decode("utf-8", "replace")
    return proc.returncode, out.rstrip()


def current_branch(cwd: Path | str) -> str:
    code, out = git(cwd, "branch", "--show-current")
    return out if code == 0 else ""


def toplevel(path: Path | str) -> Path | None:
    p = Path(path)
    code, out = git(p if p.is_dir() else p.parent, "rev-parse", "--show-toplevel")
    return Path(out).resolve() if code == 0 and out else None


def main_checkout(worktree: Path) -> Path:
    """Корень основного checkout (для linked worktree — папка, где лежит общий .git)."""
    code, common = git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if code == 0 and common:
        return Path(common).resolve().parent
    return worktree


RUFF_SELECT = "E9,F63,F7,F82"


def lint_errors(py: list[str], cwd: Path, files: list[str]) -> set[str]:
    """Синтаксис и неопределённые имена: ruff из venv проекта, без него — compile()."""
    if not files:
        return set()
    env = {k: v for k, v in os.environ.items() if k not in ("FORCE_COLOR", "CLICOLOR_FORCE")}
    env["NO_COLOR"] = "1"
    try:
        proc = subprocess.run(
            [*py, "-m", "ruff", "check", "--no-cache", "--select", RUFF_SELECT, "--output-format", "json", *files],
            cwd=str(cwd), capture_output=True, timeout=120, env=env,
        )
        if proc.returncode in (0, 1) and proc.stdout.strip():
            problems = set()
            for it in json.loads(proc.stdout.decode("utf-8", "replace")):
                p = Path(it.get("filename", ""))
                try:
                    rel = p.resolve().relative_to(Path(cwd).resolve()).as_posix()
                except ValueError:
                    rel = p.as_posix()
                problems.add(f"{rel}: {it.get('code')} {it.get('message')}")
            return problems
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    problems = set()
    for f in files:
        try:
            compile((Path(cwd) / f).read_bytes(), f, "exec")
        except SyntaxError as exc:
            problems.add(f"{f}: SyntaxError {exc.msg} (строка {exc.lineno})")
        except (OSError, ValueError):
            pass
    return problems


def project_python(worktree: Path) -> list[str]:
    """Python проекта: .venv основного checkout, затем текущий интерпретатор."""
    for root in (worktree, main_checkout(worktree)):
        for cand in (root / ".venv" / "Scripts" / "python.exe", root / ".venv" / "bin" / "python"):
            if cand.exists():
                return [str(cand)]
    return [sys.executable]
