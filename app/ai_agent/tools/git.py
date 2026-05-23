"""Git tools (read-only): git_status, git_diff, git_log.

Edit tools (git_branch, git_commit) — в Phase I.4 с HITL.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.ai_agent.safety import redact_secrets
from app.ai_agent.tools._spec import ToolContext, ToolSpec

_MAX_OUTPUT_BYTES = 50_000


async def _run_git(
    cmd: list[str], ctx: ToolContext, timeout: float | None = None
) -> tuple[int, str, str]:
    """Запустить git-команду в repo_root."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(ctx.repo_root),
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout or ctx.tool_timeout_sec
        )
    except TimeoutError:
        proc.kill()
        return -1, "", "timeout"
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


# ──────────────────────────── git_status ────────────────────────────────────


async def _run_git_status(args: dict, ctx: ToolContext) -> dict[str, Any]:
    code, out, err = await _run_git(["status", "-sb"], ctx)
    if code != 0:
        return {"ok": False, "error": err.strip() or "git status failed"}
    return {"ok": True, "output": redact_secrets(out)}


TOOL_GIT_STATUS = ToolSpec(
    name="git_status",
    spec={
        "type": "function",
        "function": {
            "name": "git_status",
            "description": (
                "Текущее состояние git: ветка + список изменённых файлов "
                "(`git status -sb`)."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    run=_run_git_status,
    is_hitl=False,
    description_short="git status -sb",
)


# ──────────────────────────── git_diff ──────────────────────────────────────


async def _run_git_diff(args: dict, ctx: ToolContext) -> dict[str, Any]:
    staged = bool(args.get("staged", False))
    path = str(args.get("path", "") or "").strip()
    stat_only = bool(args.get("stat_only", False))

    cmd = ["diff"]
    if staged:
        cmd.append("--staged")
    if stat_only:
        cmd.append("--stat")
    if path:
        cmd.extend(["--", path])

    code, out, err = await _run_git(cmd, ctx)
    if code != 0:
        return {"ok": False, "error": err.strip() or "git diff failed"}

    out = redact_secrets(out)
    truncated = False
    if len(out) > _MAX_OUTPUT_BYTES:
        out = out[:_MAX_OUTPUT_BYTES]
        truncated = True
    return {"ok": True, "output": out, "truncated": truncated}


TOOL_GIT_DIFF = ToolSpec(
    name="git_diff",
    spec={
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": (
                "Diff текущих изменений. По умолчанию unstaged. "
                "staged=true → staged. stat_only=true → только `--stat`."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "staged": {
                        "type": "boolean",
                        "description": "Показать staged changes (по умолчанию false).",
                    },
                    "path": {
                        "type": "string",
                        "description": "Ограничить diff конкретным путём (опционально).",
                    },
                    "stat_only": {
                        "type": "boolean",
                        "description": "Только статистика без содержимого diff'а.",
                    },
                },
                "required": [],
            },
        },
    },
    run=_run_git_diff,
    is_hitl=False,
    description_short="git diff",
)


# ──────────────────────────── git_log ───────────────────────────────────────


async def _run_git_log(args: dict, ctx: ToolContext) -> dict[str, Any]:
    n = int(args.get("n", 10) or 10)
    n = max(1, min(n, 50))
    path = str(args.get("path", "") or "").strip()

    cmd = ["log", f"-{n}", "--oneline", "--no-decorate"]
    if path:
        cmd.extend(["--", path])

    code, out, err = await _run_git(cmd, ctx)
    if code != 0:
        return {"ok": False, "error": err.strip() or "git log failed"}
    return {"ok": True, "commits": out.strip().split("\n") if out else []}


TOOL_GIT_LOG = ToolSpec(
    name="git_log",
    spec={
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Последние N коммитов (`git log --oneline`).",
            "parameters": {
                "type": "object",
                "properties": {
                    "n": {
                        "type": "integer",
                        "description": "Сколько коммитов (по умолчанию 10, макс 50).",
                    },
                    "path": {
                        "type": "string",
                        "description": "Ограничить путём (опционально).",
                    },
                },
                "required": [],
            },
        },
    },
    run=_run_git_log,
    is_hitl=False,
    description_short="git log --oneline",
)
