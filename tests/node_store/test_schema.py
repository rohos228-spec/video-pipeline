"""Фикстура повторяет ограничения миграции #346."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_MIGRATION_001 = _REPO / "app/orchestrator/db/migrations/001_node_defs.sql"
_MIGRATION_002 = _REPO / "app/orchestrator/db/migrations/002_node_prompt_versions.sql"

NODE_DEF_COLUMNS = [
    "id",
    "node_key",
    "group_key",
    "title",
    "origin",
    "is_new_group",
    "after_node_key",
    "status",
    "active_version",
    "created_change_id",
    "updated_change_id",
    "created_at",
    "updated_at",
    "created_by",
]

VERSION_COLUMNS = [
    "id",
    "node_key",
    "version",
    "prompt_template",
    "params_json",
    "kind",
    "patch_mode",
    "section_anchor",
    "reason",
    "parent_version",
    "base_git_sha256",
    "change_id",
    "created_at",
    "created_by",
    "created_via",
]


def _stamp() -> str:
    return "2026-10-17T00:00:00.000000Z"


def _insert_def(conn: sqlite3.Connection, node_key: str = "n_qc_vo", **extra: object) -> None:
    stamp = _stamp()
    conn.execute(
        """
        INSERT INTO node_defs (
            node_key, group_key, title, origin, is_new_group, status,
            active_version, created_at, updated_at
        ) VALUES (?, 'excel_gpt', 'QC', 'db', 0, 'active', NULL, ?, ?)
        """,
        (node_key, stamp, stamp),
    )


def test_columns_match_346_and_have_no_is_active(conn: sqlite3.Connection) -> None:
    defs = [row[1] for row in conn.execute("PRAGMA table_info(node_defs)")]
    versions = [row[1] for row in conn.execute("PRAGMA table_info(node_prompt_versions)")]
    assert defs == NODE_DEF_COLUMNS
    assert versions == VERSION_COLUMNS
    assert "is_active" not in versions
    indexes = {row[1] for row in conn.execute("PRAGMA index_list(node_prompt_versions)")}
    assert "ux_node_prompt_versions_one_active" not in indexes


def test_history_rejects_update_and_delete(conn: sqlite3.Connection) -> None:
    _insert_def(conn)
    conn.execute(
        """
        INSERT INTO node_prompt_versions (
            node_key, version, prompt_template, params_json, kind, change_id, created_at
        ) VALUES ('n_qc_vo', 1, 'prompt', '{}', 'create', 'chg-1', ?)
        """,
        (_stamp(),),
    )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE node_prompt_versions SET reason = 'x' WHERE version = 1")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("DELETE FROM node_prompt_versions WHERE version = 1")
    left = conn.execute("SELECT prompt_template FROM node_prompt_versions").fetchone()
    assert left[0] == "prompt"


def test_node_key_title_and_version_uniqueness(conn: sqlite3.Connection) -> None:
    stamp = _stamp()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO node_defs (
                node_key, group_key, title, origin, is_new_group, status,
                created_at, updated_at
            ) VALUES ('Foo', 'g', 't', 'db', 0, 'active', ?, ?)
            """,
            (stamp, stamp),
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO node_defs (
                node_key, group_key, title, origin, is_new_group, status,
                created_at, updated_at
            ) VALUES ('n_ok', 'g', ?, 'db', 0, 'active', ?, ?)
            """,
            ("т" * 121, stamp, stamp),
        )
    _insert_def(conn)
    conn.execute(
        """
        INSERT INTO node_prompt_versions (
            node_key, version, prompt_template, params_json, kind, change_id, created_at
        ) VALUES ('n_qc_vo', 1, 'a', '{}', 'create', 'chg-1', ?)
        """,
        (stamp,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO node_prompt_versions (
                node_key, version, prompt_template, params_json, kind, change_id, created_at
            ) VALUES ('n_qc_vo', 1, 'b', '{}', 'edit_prompt', 'chg-2', ?)
            """,
            (stamp,),
        )


def test_active_version_cannot_point_at_missing_or_revert(conn: sqlite3.Connection) -> None:
    stamp = _stamp()
    _insert_def(conn)
    with pytest.raises(sqlite3.IntegrityError, match="non-revert"):
        conn.execute("UPDATE node_defs SET active_version = 1 WHERE node_key = 'n_qc_vo'")
    conn.execute(
        """
        INSERT INTO node_prompt_versions (
            node_key, version, prompt_template, params_json, kind, change_id, created_at
        ) VALUES ('n_qc_vo', 1, 'git', '{}', 'revert_to_git', 'chg-1', ?)
        """,
        (stamp,),
    )
    with pytest.raises(sqlite3.IntegrityError, match="non-revert"):
        conn.execute("UPDATE node_defs SET active_version = 1 WHERE node_key = 'n_qc_vo'")
    conn.execute(
        """
        INSERT INTO node_prompt_versions (
            node_key, version, prompt_template, params_json, kind, change_id, created_at
        ) VALUES ('n_qc_vo', 2, 'prompt', '{}', 'create', 'chg-2', ?)
        """,
        (stamp,),
    )
    conn.execute("UPDATE node_defs SET active_version = 2 WHERE node_key = 'n_qc_vo'")
    assert conn.execute("SELECT active_version FROM node_defs").fetchone()[0] == 2


@pytest.mark.skipif(
    not (_MIGRATION_001.is_file() and _MIGRATION_002.is_file()),
    reason="TODO: после мёржа #346 сверить schema_346.sql с миграциями 001 и 002",
)
def test_schema_346_matches_ws3_migrations() -> None:
    fixture = (Path(__file__).parent / "schema_346.sql").read_text(encoding="utf-8")
    assert _MIGRATION_001.read_text(encoding="utf-8").strip() in fixture
    assert _MIGRATION_002.read_text(encoding="utf-8").strip() in fixture
