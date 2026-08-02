"""Safe git helpers for orchestrator code-fix → commit → push origin/main."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.code_autofix import assert_paths_allowed, repo_root


class GitOpsError(RuntimeError):
    """Ошибка git-операции оркестратора."""


def _run(args: list[str], *, cwd: Path | None = None, timeout: float = 120.0) -> str:
    root = cwd or repo_root()
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        raise GitOpsError(f"git {' '.join(args)}: timeout") from e
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[:800]
        raise GitOpsError(f"git {' '.join(args)} → {proc.returncode}: {err}")
    return (proc.stdout or "").strip()


def current_branch() -> str:
    return _run(["rev-parse", "--abbrev-ref", "HEAD"])


def ensure_main_branch() -> None:
    br = current_branch()
    if br != "main":
        raise GitOpsError(f"разрешён push только из main, сейчас ветка {br!r}")


def head_sha(*, short: bool = True) -> str:
    return _run(["rev-parse", "--short" if short else "HEAD", "HEAD"])


def changed_paths() -> list[str]:
    """Unstaged + staged + untracked (relative posix paths)."""
    out = _run(["status", "--porcelain", "-u"])
    paths: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        # XY PATH or XY ORIG -> PATH
        rest = line[3:] if len(line) > 3 else line
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        rel = rest.strip().replace("\\", "/")
        if rel:
            paths.append(rel)
    return paths


def commit_and_push(
    paths: list[str],
    message: str,
    *,
    allow_empty: bool = False,
) -> dict[str, Any]:
    """Add allowlisted paths, commit, push origin main. Returns sha info."""
    ensure_main_branch()
    msg = (message or "").strip()
    if not msg:
        raise GitOpsError("пустой commit message")
    if len(msg) > 500:
        raise GitOpsError("commit message слишком длинный (>500)")

    rels = assert_paths_allowed(paths)
    if not rels and not allow_empty:
        # fallback: only allowlisted dirty files
        dirty = [p for p in changed_paths() if _is_allowlisted_rel(p)]
        if not dirty:
            raise GitOpsError("нет файлов для коммита (allowlist пуст)")
        rels = assert_paths_allowed(dirty)

    root = repo_root()
    for rel in rels:
        _run(["add", "--", rel], cwd=root)

    staged = _run(["diff", "--cached", "--name-only"], cwd=root)
    staged_list = [p.replace("\\", "/") for p in staged.splitlines() if p.strip()]
    if not staged_list:
        raise GitOpsError("после git add staging пуст")
    assert_paths_allowed(staged_list)

    # HEREDOC-safe: pass -m once
    _run(["commit", "-m", msg], cwd=root)
    sha = head_sha(short=True)
    logger.info("git_ops: committed {} files → {}", len(staged_list), sha)

    _run(["push", "origin", "main"], cwd=root, timeout=180.0)
    logger.info("git_ops: pushed origin/main HEAD={}", sha)
    return {
        "sha": sha,
        "branch": "main",
        "files": staged_list,
        "message": msg,
    }


def _is_allowlisted_rel(rel: str) -> bool:
    try:
        assert_paths_allowed([rel])
        return True
    except Exception:  # noqa: BLE001
        return False
