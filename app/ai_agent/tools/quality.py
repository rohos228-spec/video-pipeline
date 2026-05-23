"""Quality tools: run_ruff, run_pytest, run_mypy.

Все запускают команды в subprocess с таймаутом ctx.tool_timeout_sec.
Output redacted для секретов.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.ai_agent.safety import redact_secrets
from app.ai_agent.tools._spec import ToolContext, ToolSpec

_MAX_OUTPUT_BYTES = 30_000


async def _run_subprocess(
    cmd: list[str], ctx: ToolContext
) -> tuple[int, str, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ctx.repo_root),
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=ctx.tool_timeout_sec
            )
        except TimeoutError:
            proc.kill()
            return -1, "", "timeout"
    except FileNotFoundError as e:
        return -1, "", f"command not found: {e}"
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _trim(text: str) -> str:
    text = redact_secrets(text)
    if len(text) > _MAX_OUTPUT_BYTES:
        return text[:_MAX_OUTPUT_BYTES] + "\n...[truncated]"
    return text


# ──────────────────────────── run_ruff ──────────────────────────────────────


async def _run_ruff(args: dict, ctx: ToolContext) -> dict[str, Any]:
    paths = args.get("paths") or ["."]
    if isinstance(paths, str):
        paths = [paths]
    fix = bool(args.get("fix", False))
    cmd = ["python3", "-m", "ruff", "check", "--no-cache", "--output-format=concise"]
    if fix:
        # fix НЕ применяет правки к диску! Мы запрещаем edit без HITL.
        # `fix=True` означает «покажи fix-suggestions», но не пиши на диск.
        # Используем --show-fixes без --fix.
        cmd.append("--show-fixes")
    cmd.extend(paths)

    code, out, err = await _run_subprocess(cmd, ctx)
    success = code == 0
    combined = _trim((out or "") + (("\nstderr:\n" + err) if err else ""))
    return {
        "ok": True,
        "success": success,
        "exit_code": code,
        "output": combined,
        "command": " ".join(cmd),
    }


TOOL_RUN_RUFF = ToolSpec(
    name="run_ruff",
    spec={
        "type": "function",
        "function": {
            "name": "run_ruff",
            "description": "Запустить ruff check. Используй после правок чтобы проверить линт.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Пути (по умолчанию весь репо).",
                    },
                },
                "required": [],
            },
        },
    },
    run=_run_ruff,
    is_hitl=False,
    description_short="ruff check",
)


# ──────────────────────────── run_pytest ────────────────────────────────────


async def _run_pytest(args: dict, ctx: ToolContext) -> dict[str, Any]:
    pattern = str(args.get("pattern", "") or "").strip()
    paths = args.get("paths") or ["tests/"]
    if isinstance(paths, str):
        paths = [paths]

    cmd = ["python3", "-m", "pytest", "-q", "--tb=short", "--no-header"]
    if pattern:
        cmd.extend(["-k", pattern])
    cmd.extend(paths)

    code, out, err = await _run_subprocess(cmd, ctx)
    success = code == 0
    combined = _trim((out or "") + (("\nstderr:\n" + err) if err else ""))
    return {
        "ok": True,
        "success": success,
        "exit_code": code,
        "output": combined,
        "command": " ".join(cmd),
    }


TOOL_RUN_PYTEST = ToolSpec(
    name="run_pytest",
    spec={
        "type": "function",
        "function": {
            "name": "run_pytest",
            "description": (
                "Запустить тесты. По умолчанию все из tests/. Можно сузить через pattern (-k)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Pytest -k pattern (например, 'test_safety').",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Пути (по умолчанию ['tests/']).",
                    },
                },
                "required": [],
            },
        },
    },
    run=_run_pytest,
    is_hitl=False,
    description_short="pytest -q",
)


# ──────────────────────────── run_mypy ──────────────────────────────────────


async def _run_mypy(args: dict, ctx: ToolContext) -> dict[str, Any]:
    paths = args.get("paths") or ["app"]
    if isinstance(paths, str):
        paths = [paths]

    cmd = ["python3", "-m", "mypy", "--no-error-summary", "--show-column-numbers"]
    cmd.extend(paths)

    code, out, err = await _run_subprocess(cmd, ctx)
    success = code == 0
    combined = _trim((out or "") + (("\nstderr:\n" + err) if err else ""))
    return {
        "ok": True,
        "success": success,
        "exit_code": code,
        "output": combined,
        "command": " ".join(cmd),
    }


TOOL_RUN_MYPY = ToolSpec(
    name="run_mypy",
    spec={
        "type": "function",
        "function": {
            "name": "run_mypy",
            "description": "Запустить mypy для проверки типов.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Пути (по умолчанию ['app']).",
                    },
                },
                "required": [],
            },
        },
    },
    run=_run_mypy,
    is_hitl=False,
    description_short="mypy",
)
