"""GitHub CLI tools (read-only): gh_pr_list, gh_pr_view.

gh_pr_create — в Phase I.4 с HITL.
Требует установленный `gh` CLI с авторизацией.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.ai_agent.safety import redact_secrets
from app.ai_agent.tools._spec import ToolContext, ToolSpec

_MAX_OUTPUT_BYTES = 30_000


async def _run_gh(
    cmd: list[str], ctx: ToolContext
) -> tuple[int, str, str]:
    """Запустить gh-команду."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "gh",
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
    except FileNotFoundError:
        return -1, "", "gh CLI not installed"
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


# ──────────────────────────── gh_pr_list ────────────────────────────────────


async def _run_gh_pr_list(args: dict, ctx: ToolContext) -> dict[str, Any]:
    state = str(args.get("state", "open") or "open").lower()
    if state not in {"open", "closed", "merged", "all"}:
        return {"ok": False, "error": "state must be open|closed|merged|all"}
    limit = int(args.get("limit", 20) or 20)
    limit = max(1, min(limit, 50))

    code, out, err = await _run_gh(
        ["pr", "list", "--state", state, "--limit", str(limit)],
        ctx,
    )
    if code != 0:
        return {"ok": False, "error": err.strip() or "gh pr list failed"}

    prs: list[dict] = []
    for line in out.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) >= 4:
            prs.append({
                "number": parts[0],
                "title": redact_secrets(parts[1]),
                "branch": parts[2],
                "state": parts[3],
            })
    return {"ok": True, "prs": prs, "total": len(prs)}


TOOL_GH_PR_LIST = ToolSpec(
    name="gh_pr_list",
    spec={
        "type": "function",
        "function": {
            "name": "gh_pr_list",
            "description": (
                "Список PR'ов через `gh pr list`. Возвращает number/title/branch/state."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {
                        "type": "string",
                        "description": "open | closed | merged | all (по умолчанию open).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "По умолчанию 20, макс 50.",
                    },
                },
                "required": [],
            },
        },
    },
    run=_run_gh_pr_list,
    is_hitl=False,
    description_short="gh pr list",
)


# ──────────────────────────── gh_pr_view ────────────────────────────────────


async def _run_gh_pr_view(args: dict, ctx: ToolContext) -> dict[str, Any]:
    number = str(args.get("number", "")).strip()
    if not number:
        return {"ok": False, "error": "number is required"}

    code, out, err = await _run_gh(
        ["pr", "view", number, "--json",
         "number,title,body,state,baseRefName,headRefName,createdAt,author,labels"],
        ctx,
    )
    if code != 0:
        return {"ok": False, "error": err.strip() or "gh pr view failed"}

    import json
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"json decode: {e}"}

    # redact и обрезать body
    body = data.get("body") or ""
    body = redact_secrets(body)
    if len(body) > _MAX_OUTPUT_BYTES:
        body = body[:_MAX_OUTPUT_BYTES] + "...[truncated]"
    data["body"] = body
    return {"ok": True, "pr": data}


TOOL_GH_PR_VIEW = ToolSpec(
    name="gh_pr_view",
    spec={
        "type": "function",
        "function": {
            "name": "gh_pr_view",
            "description": "Детали конкретного PR (title, body, branches, author, labels).",
            "parameters": {
                "type": "object",
                "properties": {
                    "number": {
                        "type": "string",
                        "description": "Номер PR (например, '33').",
                    },
                },
                "required": ["number"],
            },
        },
    },
    run=_run_gh_pr_view,
    is_hitl=False,
    description_short="gh pr view",
)


# ──────────────────────────── gh_pr_create (HITL) ────────────────────────────


async def _run_gh_pr_create(args: dict, ctx: "ToolContext") -> dict[str, Any]:
    """Открыть PR через gh CLI. Требует HITL."""
    title = str(args.get("title", "")).strip()
    body = str(args.get("body", "")).strip()
    base = str(args.get("base", "") or "").strip()
    draft = bool(args.get("draft", True))

    if not title:
        return {"ok": False, "error": "title is required"}

    cmd = ["pr", "create", "--title", title]
    if body:
        cmd.extend(["--body", body])
    if base:
        cmd.extend(["--base", base])
    if draft:
        cmd.append("--draft")

    code, out, err = await _run_gh(cmd, ctx)
    if code != 0:
        return {"ok": False, "error": err.strip() or "gh pr create failed"}

    # gh печатает URL новосозданного PR
    url = (out or "").strip().splitlines()[-1] if out else ""
    return {"ok": True, "url": url, "title": title, "draft": draft}


TOOL_GH_PR_CREATE = ToolSpec(
    name="gh_pr_create",
    spec={
        "type": "function",
        "function": {
            "name": "gh_pr_create",
            "description": (
                "Открыть PR из текущей ветки. По умолчанию draft. "
                "Body должен следовать .github/PULL_REQUEST_TEMPLATE.md. "
                "Требует HITL-апрува."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Заголовок PR в формате '<type>(<scope>): <описание>'.",
                    },
                    "body": {
                        "type": "string",
                        "description": "Описание PR (markdown).",
                    },
                    "base": {
                        "type": "string",
                        "description": "Base branch (по умолчанию — default репо).",
                    },
                    "draft": {
                        "type": "boolean",
                        "description": "Открыть как draft (по умолчанию true).",
                    },
                },
                "required": ["title"],
            },
        },
    },
    run=_run_gh_pr_create,
    is_hitl=True,
    description_short="gh pr create (HITL)",
)
