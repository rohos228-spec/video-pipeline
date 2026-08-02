"""Safe git helpers for orchestrator code-fix → commit → push origin/<pc-branch>."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.code_autofix import assert_paths_allowed, repo_root

ALLOWED_PUSH_BRANCHES = frozenset(
    {"main", "housepc", "tompc", "strangepc", "workpc"}
)


class GitOpsError(RuntimeError):
    """Ошибка git-операции оркестратора."""


def push_branch() -> str:
    """Целевая ветка push с этого ПК (из settings / .env)."""
    from app.settings import settings

    br = (settings.orchestrator_git_branch or "main").strip() or "main"
    if br not in ALLOWED_PUSH_BRANCHES:
        raise GitOpsError(
            f"ветка {br!r} не из allowlist {sorted(ALLOWED_PUSH_BRANCHES)}"
        )
    return br


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


def ensure_push_branch() -> str:
    """Require HEAD == configured push branch (no cross-PC push mixups)."""
    want = push_branch()
    br = current_branch()
    if br != want:
        raise GitOpsError(
            f"нужна ветка {want!r} (ORCHESTRATOR_GIT_BRANCH), сейчас {br!r}. "
            f"Сделай: git checkout {want}"
        )
    return want


def head_sha(*, short: bool = True) -> str:
    return _run(["rev-parse", "--short" if short else "HEAD", "HEAD"])


def changed_paths() -> list[str]:
    """Unstaged + staged + untracked (relative posix paths)."""
    out = _run(["status", "--porcelain", "-u"])
    paths: list[str] = []
    for line in out.splitlines():
        if not line.strip():
            continue
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
    """Add allowlisted paths, commit, push origin/<ORCHESTRATOR_GIT_BRANCH>."""
    branch = ensure_push_branch()
    msg = (message or "").strip()
    if not msg:
        raise GitOpsError("пустой commit message")
    if len(msg) > 500:
        raise GitOpsError("commit message слишком длинный (>500)")

    rels = assert_paths_allowed(paths)
    if not rels and not allow_empty:
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

    _run(["commit", "-m", msg], cwd=root)
    sha = head_sha(short=True)
    logger.info("git_ops: committed {} files → {} @{}", len(staged_list), sha, branch)

    _run(["push", "-u", "origin", branch], cwd=root, timeout=180.0)
    logger.info("git_ops: pushed origin/{} HEAD={}", branch, sha)
    return {
        "sha": sha,
        "branch": branch,
        "files": staged_list,
        "message": msg,
    }


def _is_allowlisted_rel(rel: str) -> bool:
    try:
        assert_paths_allowed([rel])
        return True
    except Exception:  # noqa: BLE001
        return False
