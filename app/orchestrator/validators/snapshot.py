"""Снимок id сцен и кадров. Только чтение, без INSERT/UPDATE/DELETE."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def load_project_snapshot(
    project_id: int,
    *,
    conn: sqlite3.Connection | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Прочитать id сцен и кадров проекта.

    Своё соединение открывается в режиме ``mode=ro``. Чужое соединение
    используется только для ``SELECT``. Если файла нет или таблиц нет,
    ``available`` = False и списки пустые — вызывающий решает, блокировать ли.
    """

    own = False
    if conn is None:
        path = _resolve_path(project_id, db_path)
        if path is None:
            return _empty(False)
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        own = True
    try:
        has_scenes = _table_exists(conn, "scenes")
        has_frames = _table_exists(conn, "frames")
        if not has_scenes and not has_frames:
            return _empty(False)
        scenes = _select_ids(conn, "scenes", project_id) if has_scenes else []
        frames = _select_ids(conn, "frames", project_id) if has_frames else []
    except sqlite3.Error:
        return _empty(False)
    finally:
        if own:
            conn.close()
    return {"scenes": scenes, "frames": frames, "available": True}


def _empty(available: bool) -> dict[str, Any]:
    return {"scenes": [], "frames": [], "available": available}


def _resolve_path(project_id: int, db_path: str | Path | None) -> Path | None:
    if db_path is not None:
        path = Path(db_path)
        return path if path.is_file() else None
    try:
        from app.project_db import resolve_project_db_path_by_id
    except Exception:  # noqa: BLE001
        return None
    found = resolve_project_db_path_by_id(int(project_id))
    if found is None:
        return None
    return found if found.is_file() else None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    return row is not None


def _select_ids(conn: sqlite3.Connection, table: str, project_id: int) -> list[dict[str, Any]]:
    if table not in {"scenes", "frames"}:
        raise sqlite3.Error(f"таблица не из снимка: {table}")
    if not _table_exists(conn, table):
        return []
    if table == "frames" and _column_exists(conn, table, "attrs"):
        rows = conn.execute(
            "SELECT id, attrs FROM frames WHERE project_id = ? ORDER BY id",
            (int(project_id),),
        ).fetchall()
        found: list[dict[str, Any]] = []
        for row_id, attrs in rows:
            found.append({"id": row_id})
            for shot_id in _kadry_ids(attrs):
                found.append({"id": shot_id})
        return found
    rows = conn.execute(
        f"SELECT id FROM {table} WHERE project_id = ? ORDER BY id",  # noqa: S608 — имя таблицы из белого списка
        (int(project_id),),
    ).fetchall()
    return [{"id": row[0]} for row in rows]


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    if table not in {"scenes", "frames"}:
        return False
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()  # noqa: S608 — имя из белого списка
    return any(str(row[1]) == column for row in info)


def _kadry_ids(attrs: Any) -> list[Any]:
    if isinstance(attrs, str):
        try:
            attrs = json.loads(attrs)
        except json.JSONDecodeError:
            return []
    if not isinstance(attrs, dict):
        return []
    shots = attrs.get("кадры")
    if not isinstance(shots, list):
        return []
    found: list[Any] = []
    for shot in shots:
        if isinstance(shot, dict):
            raw = shot.get("id")
            if raw in (None, ""):
                raw = shot.get("shot_id", shot.get("frame_id"))
        elif isinstance(shot, (str, int)):
            raw = shot
        else:
            continue
        if raw in (None, ""):
            continue
        found.append(raw if isinstance(raw, int) else str(raw))
    return found
