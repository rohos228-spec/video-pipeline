"""Триаж OPEN PR'ов и старых веток (Phase H).

Использование:
    python -m scripts.triage_prs                    # вывод таблицы в stdout
    python -m scripts.triage_prs --markdown -o triage.md   # MD для issue
    python -m scripts.triage_prs --json                    # JSON для CI

Что собирает (через `gh` CLI, read-only):
1. Все OPEN PR'ы с:
   - branch, title, created/updated даты,
   - возраст в днях,
   - предложенное решение (merge / rebase / close as stale / close as superseded),
   - тема (выводится из title через ключевые слова).

2. Все ветки в репо (если --branches):
   - cursor/audit-* (массовый шум),
   - devin/<timestamp>-* без открытого PR,
   - предложенное решение (delete / cherry-pick to rollup / keep).

Скрипт ТОЛЬКО показывает рекомендации, **ничего не удаляет**. Решение —
за человеком. Для удаления используется отдельная команда `gh api -X DELETE`.

См. PLAN.md §10 (Phase H).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class PRInfo:
    number: int
    title: str
    branch: str
    base: str
    state: str
    author: str
    created_at: str
    updated_at: str
    age_days: int
    is_draft: bool

    def suggestion(self) -> tuple[str, str]:
        """Возвращает (action, reason)."""
        t = self.title.lower()
        b = self.branch.lower()
        age = self.age_days

        # Чем старше — тем подозрительнее
        if age > 60:
            return ("close-as-stale", f"PR старше 60 дней (age={age}d)")
        if age > 30 and not self.is_draft:
            return ("rebase-or-close", f"PR старше 30 дней без активности (age={age}d)")

        # Дубли тем
        if "video-403" in t or "403" in t.replace("video", ""):
            return ("close-as-superseded", "video-403 уже решено в canonical (physical CDP clicks)")
        if "manual-walk" in t or "manual_walk" in t:
            return ("close-as-superseded", "manual-walk объединён в canonical")
        if "mass-creation" in t or "mass-gen" in t:
            return ("close-as-superseded", "mass-creation уже в canonical")
        if "outsee" in t and ("download" in t or "click" in t):
            return ("close-as-superseded", "outsee download/click — уже в canonical")
        if "visual_lab" in t or "visual lab" in t:
            return ("close-as-superseded", "visual_lab уже в canonical")
        if "per-frame hitl" in t or "per-video" in t:
            return ("close-as-superseded", "per-frame HITL смержен через #35")
        if "gpt-checks" in t or "gpt checks" in t:
            return ("close-as-superseded", "GPT-checks уже в canonical")

        if "audit" in b and b.startswith("cursor/"):
            return (
                "close-and-delete-branch",
                "cursor/audit-* серия — все 25+ закрываем массово",
            )

        # AGENTS.md / dev environment метаданные
        if "agents.md" in t.lower() or "cursor cloud" in t.lower():
            return (
                "review-then-merge-or-close",
                "Новый AGENTS.md / dev environment — оценить совместимость с этим PR #39",
            )

        return ("review", f"требует ручного просмотра (age={age}d)")


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _parse_iso(s: str) -> datetime:
    # GitHub API даёт 2026-05-22T13:55:21Z
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


async def _run_gh(args: list[str], timeout: float = 30) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "gh", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return -1, "", "timeout"
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def fetch_prs(state: str = "open", limit: int = 100) -> list[PRInfo]:
    code, out, err = await _run_gh([
        "pr", "list",
        "--state", state,
        "--limit", str(limit),
        "--json",
        "number,title,headRefName,baseRefName,state,author,createdAt,updatedAt,isDraft",
    ])
    if code != 0:
        print(f"gh error: {err}", file=sys.stderr)
        return []

    raw = json.loads(out)
    now = _now_utc()
    result = []
    for pr in raw:
        created = _parse_iso(pr["createdAt"])
        age = (now - created).days
        result.append(PRInfo(
            number=pr["number"],
            title=pr["title"],
            branch=pr["headRefName"],
            base=pr["baseRefName"],
            state=pr["state"],
            author=pr["author"]["login"] if pr.get("author") else "?",
            created_at=pr["createdAt"][:10],
            updated_at=pr["updatedAt"][:10],
            age_days=age,
            is_draft=pr.get("isDraft", False),
        ))
    return result


async def fetch_stale_branches(*, prefixes: Iterable[str] = ("cursor/audit-",), age_days: int = 0) -> list[dict]:
    """Список веток подходящих под префиксы (через gh api).

    Возвращает list of dict {name, sha, last_commit_date}.
    """
    # Получаем все ветки (max 100 за запрос, paginate)
    code, out, err = await _run_gh([
        "api", "--paginate",
        "repos/{owner}/{repo}/branches",
        "--jq",
        ".[] | {name: .name, sha: .commit.sha}",
    ])
    if code != 0:
        print(f"gh api error: {err}", file=sys.stderr)
        return []

    # Каждая строка — отдельный JSON-объект (--jq stream)
    branches = []
    for line in out.strip().split("\n"):
        if not line:
            continue
        try:
            branches.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    matching = [
        b for b in branches
        if any(b["name"].startswith(p) for p in prefixes)
    ]
    return matching


def print_text(prs: list[PRInfo]) -> None:
    if not prs:
        print("(нет OPEN PR'ов)")
        return

    # Группируем по предложенному действию
    by_action: dict[str, list[PRInfo]] = {}
    for pr in prs:
        action, _ = pr.suggestion()
        by_action.setdefault(action, []).append(pr)

    print(f"=== Triage {len(prs)} OPEN PR'ов ===\n")
    for action, group in sorted(by_action.items(), key=lambda kv: -len(kv[1])):
        print(f"\n## {action} ({len(group)})")
        for pr in group:
            _, reason = pr.suggestion()
            print(
                f"  #{pr.number:>3}  age={pr.age_days:>3}d  "
                f"{pr.title[:70]:<70}  ({reason})"
            )

    print("\n\n=== Сводка ===")
    print(f"Total OPEN: {len(prs)}")
    for action, group in sorted(by_action.items(), key=lambda kv: -len(kv[1])):
        print(f"  {action}: {len(group)}")


def print_markdown(prs: list[PRInfo], branches: list[dict]) -> str:
    """Markdown-отчёт для GH Issue."""
    lines: list[str] = []
    lines.append("# Triage: PR'ы и ветки video-pipeline")
    lines.append("")
    lines.append(f"Сгенерировано: {_now_utc().isoformat()}")
    lines.append("")
    lines.append("## Open PR'ы")
    lines.append("")
    lines.append("| # | age | branch | title | action | reason |")
    lines.append("|---|---|---|---|---|---|")
    for pr in sorted(prs, key=lambda p: p.age_days, reverse=True):
        action, reason = pr.suggestion()
        title = pr.title.replace("|", "\\|")
        branch = pr.branch.replace("|", "\\|")
        reason = reason.replace("|", "\\|")
        lines.append(
            f"| #{pr.number} | {pr.age_days}d | `{branch}` | {title} | "
            f"**{action}** | {reason} |"
        )
    lines.append("")

    # Сводка
    by_action: dict[str, int] = {}
    for pr in prs:
        action, _ = pr.suggestion()
        by_action[action] = by_action.get(action, 0) + 1
    lines.append("### Сводка")
    lines.append("")
    for action, c in sorted(by_action.items(), key=lambda kv: -kv[1]):
        lines.append(f"- **{action}**: {c} PR'ов")
    lines.append("")

    if branches:
        lines.append(f"## Ветки `cursor/audit-*` ({len(branches)})")
        lines.append("")
        lines.append("Все рекомендуется удалить массово после rollup'а полезных правок:")
        lines.append("")
        lines.append("```bash")
        for b in branches[:30]:
            bname = b["name"]
            lines.append(
                "gh api -X DELETE "
                "'repos/{owner}/{repo}/git/refs/heads/" + bname + "'"
            )
        if len(branches) > 30:
            lines.append(f"# ... и ещё {len(branches) - 30} веток")
        lines.append("```")
        lines.append("")

    lines.append("## Как использовать этот отчёт")
    lines.append("")
    lines.append("1. Закрыть PR'ы с `close-as-superseded` массово через GitHub UI.")
    lines.append("2. PR'ы с `close-as-stale` — закрыть с комментарием «закрываю как stale».")
    lines.append("3. `rebase-or-close` — попросить автора rebase'ить или закрыть.")
    lines.append("4. `review` — посмотреть руками, решить.")
    lines.append("5. Ветки `cursor/audit-*` — удалить массово (если не нужны cherry-pick'и).")
    lines.append("")
    return "\n".join(lines)


def print_json(prs: list[PRInfo], branches: list[dict]) -> str:
    data = {
        "generated_at": _now_utc().isoformat(),
        "prs": [
            {
                "number": pr.number,
                "title": pr.title,
                "branch": pr.branch,
                "base": pr.base,
                "author": pr.author,
                "created_at": pr.created_at,
                "updated_at": pr.updated_at,
                "age_days": pr.age_days,
                "is_draft": pr.is_draft,
                "suggestion": {
                    "action": pr.suggestion()[0],
                    "reason": pr.suggestion()[1],
                },
            }
            for pr in prs
        ],
        "stale_branches": [b["name"] for b in branches],
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Триаж PR'ов и веток video-pipeline.")
    parser.add_argument("--state", default="open", choices=["open", "closed", "all"])
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument(
        "--branches",
        action="store_true",
        help="Также собрать ветки cursor/audit-* / devin/<timestamp>-* (медленнее).",
    )
    parser.add_argument("--markdown", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("-o", "--output", default=None)
    args = parser.parse_args(argv)

    async def _go() -> tuple[list[PRInfo], list[dict]]:
        prs_task = fetch_prs(state=args.state, limit=args.limit)
        if args.branches:
            br_task = fetch_stale_branches(prefixes=("cursor/audit-", "cursor/full-audit-", "cursor/audit-fix"))
            return await asyncio.gather(prs_task, br_task)
        return await prs_task, []

    prs, branches = asyncio.run(_go())

    if args.json:
        out = print_json(prs, branches)
    elif args.markdown:
        out = print_markdown(prs, branches)
    else:
        print_text(prs)
        return 0

    if args.output:
        with open(args.output, "w") as f:
            f.write(out)
        print(f"saved to {args.output}", file=sys.stderr)
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
