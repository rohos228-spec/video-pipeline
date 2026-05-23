"""Дамп AI-сессии в JSON для post-mortem'а.

Использование:
    python -m scripts.ai_dump <session_id>           # на stdout
    python -m scripts.ai_dump <session_id> -o file.json
    python -m scripts.ai_dump --list                 # последние 20 сессий
    python -m scripts.ai_dump --list --chat 279887118 --status failed

Все запросы — read-only к БД через app.db.session_scope.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.ai_agent.audit import serialize_session_for_dump
from app.db import session_scope
from app.models import AISession


async def _dump_one(session_id: int) -> dict:
    async with session_scope() as db:
        stmt = (
            select(AISession)
            .where(AISession.id == session_id)
            .options(
                selectinload(AISession.messages),
                selectinload(AISession.tool_calls),
            )
        )
        result = await db.execute(stmt)
        s = result.scalar_one_or_none()
        if s is None:
            return {"error": f"session #{session_id} not found"}
        return serialize_session_for_dump(s)


async def _list_sessions(
    *,
    limit: int = 20,
    chat_id: int | None = None,
    status: str | None = None,
) -> list[dict]:
    async with session_scope() as db:
        stmt = select(AISession).order_by(AISession.id.desc()).limit(limit)
        if chat_id is not None:
            stmt = stmt.where(AISession.chat_id == chat_id)
        if status:
            from app.models import AISessionStatus

            stmt = stmt.where(AISession.status == AISessionStatus(status))
        rows = (await db.execute(stmt)).scalars().all()
    return [
        {
            "id": s.id,
            "chat_id": s.chat_id,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "finished_at": s.finished_at.isoformat() if s.finished_at else None,
            "status": s.status.value,
            "mode": s.mode.value,
            "model": s.model,
            "step_count": s.step_count,
            "tokens": s.total_tokens_in + s.total_tokens_out,
            "cost_rub": s.cost_rub,
            "initial_query": (s.initial_query or "")[:80],
            "final_answer_preview": (s.final_answer or "")[:80],
        }
        for s in rows
    ]


def _print_pretty_session(data: dict) -> None:
    """Человекочитаемый вывод (для stdout)."""
    if "error" in data:
        print(data["error"])
        return

    print(f"== AI Session #{data['id']} ({data['status']}) ==")
    print(f"  model    : {data['model']}")
    print(f"  mode     : {data['mode']}")
    print(f"  chat_id  : {data['chat_id']}")
    print(f"  branch   : {data.get('branch') or '—'}")
    print(f"  started  : {data['started_at']}")
    print(f"  finished : {data['finished_at']}")
    print(f"  steps    : {data['step_count']}")
    print(
        f"  tokens   : {data['total_tokens_in']} in + "
        f"{data['total_tokens_out']} out"
    )
    print(f"  cost     : {data['cost_rub']:.4f}₽")
    print()
    print(f"  initial_query: {data['initial_query'][:200]}")
    if data.get("final_answer"):
        print(f"  final_answer : {data['final_answer'][:500]}")
    print()
    print(f"  messages ({len(data['messages'])}):")
    for m in data["messages"][:50]:
        role = m["role"]
        snippet = (m.get("content") or "").replace("\n", " ")[:100]
        tool = f" [{m.get('tool_name')}]" if m.get("tool_name") else ""
        tk = m.get("tokens", [0, 0])
        print(f"    [{m['id']:4}] {role}{tool} ({tk[0]}+{tk[1]}): {snippet}")
    print()
    print(f"  tool_calls ({len(data['tool_calls'])}):")
    for tc in data["tool_calls"][:50]:
        args = json.dumps(tc.get("args") or {}, ensure_ascii=False)[:80]
        print(f"    [{tc['id']:4}] {tc['tool']} {tc['status']} args={args}")


def _print_pretty_list(rows: list[dict]) -> None:
    if not rows:
        print("(no sessions)")
        return
    print(f"{'id':>5} {'status':<12} {'mode':<10} {'model':<22} {'steps':>5} {'tokens':>8} {'cost':>8}  query")
    for r in rows:
        print(
            f"{r['id']:>5} {r['status']:<12} {r['mode']:<10} {r['model']:<22} "
            f"{r['step_count']:>5} {r['tokens']:>8} {r['cost_rub']:>7.2f}₽  "
            f"{r['initial_query']}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Дамп AI-сессий video-pipeline.")
    parser.add_argument("session_id", type=int, nargs="?", help="ID сессии для дампа.")
    parser.add_argument("--list", action="store_true", help="Список сессий.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--chat", type=int, default=None, help="Фильтр по chat_id.")
    parser.add_argument("--status", default=None, help="Фильтр: active|completed|cancelled|failed.")
    parser.add_argument(
        "-o", "--output", default=None, help="Сохранить JSON в файл."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Вывести в JSON вместо pretty-print.",
    )
    args = parser.parse_args(argv)

    if args.list:
        rows = asyncio.run(
            _list_sessions(limit=args.limit, chat_id=args.chat, status=args.status)
        )
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            _print_pretty_list(rows)
        return 0

    if args.session_id is None:
        parser.error("session_id required (или --list)")
        return 2  # pragma: no cover

    data = asyncio.run(_dump_one(args.session_id))
    if args.output:
        with open(args.output, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"saved to {args.output}", file=sys.stderr)
        return 0

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_pretty_session(data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
