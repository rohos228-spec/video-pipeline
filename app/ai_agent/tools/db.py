"""DB tools для AI-агента: describe_db, db_query (только SELECT).

Работаем с тем же state.db что и пайплайн. Read-only.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from app.ai_agent.tools._spec import ToolContext, ToolSpec

_MAX_ROWS = 100
_MAX_CELL_LEN = 500  # обрезаем длинные строковые значения

# Распознавание не-SELECT запросов
_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|create|attach|detach|"
    r"pragma|replace|vacuum|reindex)\b",
    re.IGNORECASE,
)


def _resolve_db_path(repo_root: Path) -> Path:
    """Вычислить путь к SQLite БД."""
    import os

    raw = os.environ.get("SQLITE_PATH", "./data/state.db").strip()
    p = Path(raw)
    if not p.is_absolute():
        p = (repo_root / p).resolve()
    return p


# ──────────────────────────── describe_db ───────────────────────────────────


async def _run_describe_db(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    db_path = _resolve_db_path(ctx.repo_root)
    if not db_path.exists():
        return {
            "ok": False,
            "error": f"db not found: {db_path}",
            "hint": "БД создаётся при первом запуске бота (python -m app.main).",
        }

    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            tables_rows = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name"
            ).fetchall()
            tables: list[dict[str, Any]] = []
            for tr in tables_rows:
                tname = tr["name"]
                cols = conn.execute(f"PRAGMA table_info('{tname}')").fetchall()
                row_count = conn.execute(
                    f"SELECT count(*) AS c FROM '{tname}'"
                ).fetchone()["c"]
                tables.append({
                    "name": tname,
                    "row_count": row_count,
                    "columns": [
                        {
                            "name": c["name"],
                            "type": c["type"],
                            "nullable": not c["notnull"],
                            "pk": bool(c["pk"]),
                        }
                        for c in cols
                    ],
                })
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"db error: {e}"}

    return {
        "ok": True,
        "db_path": str(db_path.relative_to(ctx.repo_root))
        if db_path.is_relative_to(ctx.repo_root)
        else str(db_path),
        "tables": tables,
    }


TOOL_DESCRIBE_DB = ToolSpec(
    name="describe_db",
    spec={
        "type": "function",
        "function": {
            "name": "describe_db",
            "description": (
                "Описать схему БД: список таблиц с колонками, типами, и числом строк. "
                "Используй перед db_query чтобы понять, какие таблицы есть."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    run=_run_describe_db,
    is_hitl=False,
    description_short="Схема БД (таблицы и колонки)",
)


# ──────────────────────────── db_query (SELECT only) ────────────────────────


async def _run_db_query(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    sql = str(args.get("sql", "")).strip()
    if not sql:
        return {"ok": False, "error": "sql is required"}

    # Жёсткая проверка: запрос должен начинаться с SELECT или WITH (CTE).
    sql_stripped = sql.lstrip().lower()
    if not (sql_stripped.startswith("select") or sql_stripped.startswith("with")):
        return {
            "ok": False,
            "error": "only SELECT (или WITH ... SELECT) разрешён. INSERT/UPDATE/DELETE/DROP запрещены.",
        }

    # Доп. защита: ищем запрещённые keywords.
    if _FORBIDDEN_SQL.search(sql):
        return {
            "ok": False,
            "error": "запрещённое SQL-keyword обнаружено (insert/update/delete/drop/alter/pragma/etc).",
        }

    # Запрет ;-разделённых запросов (multistatement).
    semicolons = [c for c in sql if c == ";"]
    if len(semicolons) > 1 or (len(semicolons) == 1 and not sql.rstrip().endswith(";")):
        return {"ok": False, "error": "multi-statement SQL запрещён"}

    db_path = _resolve_db_path(ctx.repo_root)
    if not db_path.exists():
        return {"ok": False, "error": f"db not found: {db_path}"}

    limit = min(int(args.get("limit", 50) or 50), _MAX_ROWS)

    try:
        # mode=ro гарантирует read-only на уровне sqlite.
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.execute(sql)
            rows = cur.fetchmany(limit + 1)
            columns = [d[0] for d in cur.description] if cur.description else []
    except sqlite3.Error as e:
        return {"ok": False, "error": f"sqlite: {e}"}

    truncated = len(rows) > limit
    rows = rows[:limit]

    def _trim_cell(v: Any) -> Any:
        if isinstance(v, str) and len(v) > _MAX_CELL_LEN:
            return v[:_MAX_CELL_LEN] + "...[truncated]"
        return v

    data = [{k: _trim_cell(r[k]) for k in r} for r in rows]

    return {
        "ok": True,
        "columns": columns,
        "rows": data,
        "row_count": len(data),
        "truncated": truncated,
        "limit": limit,
    }


TOOL_DB_QUERY = ToolSpec(
    name="db_query",
    spec={
        "type": "function",
        "function": {
            "name": "db_query",
            "description": (
                "Выполнить read-only SQL SELECT/WITH запрос к state.db. "
                "INSERT/UPDATE/DELETE/DROP запрещены (вернётся error). "
                "Многосрочные запросы запрещены. Лимит 100 строк."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "SELECT-запрос (можно с JOIN, WHERE, GROUP BY, ORDER BY, LIMIT).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Лимит строк (по умолчанию 50, макс 100).",
                    },
                },
                "required": ["sql"],
            },
        },
    },
    run=_run_db_query,
    is_hitl=False,
    description_short="SELECT-запрос к БД",
)
