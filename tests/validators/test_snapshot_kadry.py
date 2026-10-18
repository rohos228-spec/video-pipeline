"""Снимок SQLite видит id шотов внутри frames.attrs.кадры."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.orchestrator.validators.snapshot import load_project_snapshot


def test_no_deletions_db_snapshot_sees_kadry_ids(tmp_path: Path) -> None:
    path = tmp_path / "project.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE scenes (id INTEGER PRIMARY KEY, project_id INTEGER)")
    conn.execute(
        "CREATE TABLE frames (id INTEGER PRIMARY KEY, project_id INTEGER, attrs TEXT)"
    )
    attrs = json.dumps(
        {
            "кадры": [
                {"id": "1-K1", "закадр": "вошёл"},
                {"id": "1-K2", "активен": False},
                "мусор",
            ]
        },
        ensure_ascii=False,
    )
    conn.execute("INSERT INTO scenes (id, project_id) VALUES (1, 9)")
    conn.execute(
        "INSERT INTO frames (id, project_id, attrs) VALUES (10, 9, ?), (11, 8, ?)",
        (attrs, json.dumps({"кадры": [{"id": "чужой"}]})),
    )
    conn.commit()
    conn.close()
    before = path.read_bytes()
    snap = load_project_snapshot(9, db_path=path)
    assert snap["available"] is True
    assert [row["id"] for row in snap["scenes"]] == [1]
    ids = [row["id"] for row in snap["frames"]]
    assert 10 in ids
    assert "1-K1" in ids
    assert "1-K2" in ids
    assert "чужой" not in ids
    assert path.read_bytes() == before
