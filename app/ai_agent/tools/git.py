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


# ──────────────────────────── git_branch (HITL) ──────────────────────────────


async def _run_git_branch(args: dict, ctx: ToolContext) -> dict[str, Any]:
    """Создать новую ветку и переключиться на неё (с HITL-апрувом)."""
    name = str(args.get("name", "")).strip()
    if not name:
        return {"ok": False, "error": "name is required"}

    # Validate naming: не пускаем legacy/, main, vetka-final напрямую
    forbidden_prefixes = ("main", "vetka-final", "legacy/")
    if any(name == p or name.startswith(p) for p in forbidden_prefixes):
        return {
            "ok": False,
            "error": f"имя ветки '{name}' запрещено (см. AGENTS.md naming)",
        }

    base = str(args.get("base", "") or "").strip()
    cmd = ["checkout", "-b", name]
    if base:
        cmd.append(base)
    code, out, err = await _run_git(cmd, ctx)
    if code != 0:
        return {"ok": False, "error": err.strip() or "git checkout -b failed"}
    return {"ok": True, "branch": name, "output": out + err}


TOOL_GIT_BRANCH = ToolSpec(
    name="git_branch",
    spec={
        "type": "function",
        "function": {
            "name": "git_branch",
            "description": (
                "Создать и переключиться на новую ветку (`git checkout -b`). "
                "Запрещены имена: main, vetka-final, legacy/*. "
                "Используй префиксы: feat/, fix/, chore/, agent/ai-*. "
                "Требует HITL-апрува."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Имя новой ветки (например, fix/telegram-buttons-back).",
                    },
                    "base": {
                        "type": "string",
                        "description": "От какой ветки/коммита создать (по умолчанию current HEAD).",
                    },
                },
                "required": ["name"],
            },
        },
    },
    run=_run_git_branch,
    is_hitl=True,
    description_short="Создать ветку (HITL)",
)


# ──────────────────────────── git_commit (HITL) ──────────────────────────────


async def _run_git_commit(args: dict, ctx: ToolContext) -> dict[str, Any]:
    """Закоммитить изменения. Никаких push'ей — это делает owner вручную."""
    message = str(args.get("message", "")).strip()
    if not message:
        return {"ok": False, "error": "message is required"}
    if len(message) > 2000:
        return {"ok": False, "error": "commit message too long (max 2000 chars)"}

    paths = args.get("paths") or []
    if isinstance(paths, str):
        paths = [paths]

    # Сначала git add
    if paths:
        add_cmd = ["add", "--", *[str(p) for p in paths]]
    else:
        add_cmd = ["add", "-A"]
    code, out, err = await _run_git(add_cmd, ctx)
    if code != 0:
        return {"ok": False, "error": f"git add failed: {err.strip()}"}

    # Проверка что есть что коммитить
    code_check, out_check, _ = await _run_git(["diff", "--cached", "--name-only"], ctx)
    if code_check == 0 and not out_check.strip():
        return {"ok": False, "error": "ничего не закоммичено (нет staged changes)"}

    # Сам commit
    code, out, err = await _run_git(["commit", "-m", message], ctx)
    if code != 0:
        return {"ok": False, "error": err.strip() or "git commit failed"}

    # SHA нового коммита
    code, sha, _ = await _run_git(["rev-parse", "--short", "HEAD"], ctx)
    sha = sha.strip()

    return {
        "ok": True,
        "sha": sha,
        "message": message,
        "output": redact_secrets(out),
    }


TOOL_GIT_COMMIT = ToolSpec(
    name="git_commit",
    spec={
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": (
                "Сделать коммит. По умолчанию `git add -A` всё. "
                "Стиль message: '<type>(<scope>): <описание>'. "
                "Pushed НЕ делается — owner пушит вручную. "
                "Требует HITL-апрува."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Commit message (см. AGENTS.md §15 стиль).",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Опционально: только эти пути (по умолчанию все changes).",
                    },
                },
                "required": ["message"],
            },
        },
    },
    run=_run_git_commit,
    is_hitl=True,
    description_short="git commit (HITL)",
)
