"""resolve_node / get_node: git, db-снимок, stale_base."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.orchestrator.node_store import get_node, resolve_node
from app.orchestrator.node_store._db import NodeNotFoundError, NodeStoreError
from app.orchestrator.node_store.contracts_shim import NodeDef
from app.orchestrator.node_store.writer import create_node


def test_node_def_fields_match_pr_346() -> None:
    assert set(NodeDef.model_fields) == {
        "node_key",
        "prompt_template",
        "params",
        "source",
        "version",
        "db_version",
        "git_sha256",
        "stale_base",
    }


def _write_md(templates: Path, node_key: str, text: str, group: str = "excel_gpt") -> str:
    folder = templates / group
    folder.mkdir(exist_ok=True)
    payload = text.encode("utf-8")
    (folder / f"{node_key}.md").write_bytes(payload)
    return hashlib.sha256(payload).hexdigest()


def test_resolve_git_when_no_db_row(conn: sqlite3.Connection, templates: Path) -> None:
    digest = _write_md(templates, "n_qc_vo", "git prompt\n")
    node = resolve_node("n_qc_vo", conn, templates_root=templates)
    assert node.source == "git"
    assert node.prompt_template == "git prompt\n"
    assert node.params == {}
    assert node.version == 0
    assert node.db_version is None
    assert node.git_sha256 == digest
    assert node.stale_base is False
    assert get_node("n_qc_vo", conn, templates_root=templates).model_dump() == node.model_dump()


def test_resolve_db_snapshot_over_git_keeps_current_hash(
    conn: sqlite3.Connection, templates: Path
) -> None:
    digest = _write_md(templates, "n_qc_vo", "git prompt\n")
    create_node(
        conn,
        node_key="n_qc_vo",
        group_key="excel_gpt",
        title="QC",
        prompt_template="db prompt",
        params={"model": "db-model"},
        change_id="chg-1",
        origin="git_override",
        templates_root=templates,
    )
    node = resolve_node("n_qc_vo", conn, templates_root=templates)
    assert node.source == "db"
    assert node.prompt_template == "db prompt"
    assert node.params == {"model": "db-model"}
    assert node.version == 1
    assert node.db_version == 1
    assert node.git_sha256 == digest
    assert node.stale_base is False


def test_db_params_replace_git_params(conn: sqlite3.Connection, templates: Path) -> None:
    folder = templates / "excel_gpt"
    folder.mkdir()
    payload = {
        "prompt_template": "from git",
        "params": {"model": "git-model", "temperature": 0.1},
        "group_key": "excel_gpt",
        "title": "QC",
    }
    (folder / "n_qc_vo.json").write_text(json.dumps(payload), encoding="utf-8")
    create_node(
        conn,
        node_key="n_qc_vo",
        group_key="excel_gpt",
        title="QC",
        prompt_template="from db",
        params={"model": "db-model"},
        change_id="chg-1",
        origin="git_override",
        templates_root=templates,
    )
    node = resolve_node("n_qc_vo", conn, templates_root=templates)
    assert node.prompt_template == "from db"
    assert node.params == {"model": "db-model"}
    assert "temperature" not in node.params


def test_stale_base_when_git_file_changes(conn: sqlite3.Connection, templates: Path) -> None:
    path_hash = _write_md(templates, "n_qc_vo", "v1\n")
    create_node(
        conn,
        node_key="n_qc_vo",
        group_key="excel_gpt",
        title="QC",
        prompt_template="db",
        params={"model": "m"},
        change_id="chg-1",
        origin="git_override",
        templates_root=templates,
    )
    stored = conn.execute(
        "SELECT base_git_sha256 FROM node_prompt_versions WHERE version = 1"
    ).fetchone()
    assert stored[0] == path_hash
    new_bytes = b"v2-changed\n"
    (templates / "excel_gpt" / "n_qc_vo.md").write_bytes(new_bytes)
    node = resolve_node("n_qc_vo", conn, templates_root=templates)
    assert node.git_sha256 == hashlib.sha256(new_bytes).hexdigest()
    assert node.stale_base is True
    assert node.prompt_template == "db"


def test_missing_git_is_not_stale_until_file_appears(
    conn: sqlite3.Connection, templates: Path
) -> None:
    create_node(
        conn,
        node_key="n_only_db",
        group_key="excel_gpt",
        title="Только БД",
        prompt_template="из базы",
        params={"model": "m"},
        change_id="chg-1",
        templates_root=templates,
    )
    node = resolve_node("n_only_db", conn, templates_root=templates)
    assert node.source == "db"
    assert node.git_sha256 is None
    assert node.stale_base is False
    digest = _write_md(templates, "n_only_db", "появился git\n")
    stale = resolve_node("n_only_db", conn, templates_root=templates)
    assert stale.git_sha256 == digest
    assert stale.stale_base is True
    assert stale.prompt_template == "из базы"


def test_unknown_node_and_ambiguous_template(conn: sqlite3.Connection, templates: Path) -> None:
    with pytest.raises(NodeNotFoundError):
        resolve_node("n_missing", conn, templates_root=templates)
    (templates / "a").mkdir()
    (templates / "b").mkdir()
    (templates / "a" / "n_qc_vo.md").write_text("a", encoding="utf-8")
    (templates / "b" / "n_qc_vo.md").write_text("b", encoding="utf-8")
    with pytest.raises(NodeStoreError, match="несколько git-шаблонов"):
        resolve_node("n_qc_vo", conn, templates_root=templates)


def test_bad_node_key_rejected(conn: sqlite3.Connection, templates: Path) -> None:
    with pytest.raises(NodeStoreError, match="node_key"):
        resolve_node("Foo", conn, templates_root=templates)


def test_null_pointer_without_git_is_not_an_empty_node(
    conn: sqlite3.Connection, templates: Path
) -> None:
    create_node(
        conn,
        node_key="n_only_db",
        group_key="excel_gpt",
        title="Только БД",
        prompt_template="из базы",
        change_id="chg-1",
        templates_root=templates,
    )
    conn.execute("UPDATE node_defs SET active_version = NULL WHERE node_key = 'n_only_db'")
    with pytest.raises(NodeNotFoundError, match="не найдена в git"):
        resolve_node("n_only_db", conn, templates_root=templates)
    with pytest.raises(NodeNotFoundError, match="не найдена в git"):
        get_node("n_only_db", conn, templates_root=templates)


def test_disabled_node_still_resolves(conn: sqlite3.Connection, templates: Path) -> None:
    create_node(
        conn,
        node_key="n_only_db",
        group_key="excel_gpt",
        title="Только БД",
        prompt_template="из базы",
        change_id="chg-1",
        templates_root=templates,
    )
    conn.execute("UPDATE node_defs SET status = 'disabled' WHERE node_key = 'n_only_db'")
    node = resolve_node("n_only_db", conn, templates_root=templates)
    assert node.source == "db"
    assert node.prompt_template == "из базы"
    assert node.db_version == 1


def test_broken_params_json_is_node_store_error(conn: sqlite3.Connection, templates: Path) -> None:
    create_node(
        conn,
        node_key="n_only_db",
        group_key="excel_gpt",
        title="Только БД",
        prompt_template="из базы",
        change_id="chg-1",
        templates_root=templates,
    )
    conn.execute(
        """
        INSERT INTO node_prompt_versions (
            node_key, version, prompt_template, params_json, kind, change_id, created_at
        ) VALUES ('n_only_db', 2, 'из базы', 'не json', 'edit_params', 'chg-2', '2026-10-17T00:00:00.000000Z')
        """
    )
    conn.execute("UPDATE node_defs SET active_version = 2 WHERE node_key = 'n_only_db'")
    with pytest.raises(NodeStoreError, match="не JSON"):
        resolve_node("n_only_db", conn, templates_root=templates)
