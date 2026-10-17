"""Запись версий: операции, порядок, атомарность, неизменяемость, revert."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.orchestrator.node_store import resolve_node, writer
from app.orchestrator.node_store._db import NodeExistsError, NodeNotFoundError, NodeStoreError

_GIT = """# Вход

## Камера
старый текст
ещё

## Действие
не трогать
"""


def _git(templates: Path, text: str = _GIT, node_key: str = "n_qc_vo") -> None:
    folder = templates / "excel_gpt"
    folder.mkdir(exist_ok=True)
    (folder / f"{node_key}.md").write_text(text, encoding="utf-8")


def _create(conn: sqlite3.Connection, templates: Path, **overrides: object) -> int:
    payload: dict[str, object] = {
        "node_key": "n_qc_vo",
        "group_key": "excel_gpt",
        "title": "QC",
        "prompt_template": "версия один",
        "params": {"model": "gpt-5.6-sol", "reasoning_effort": "high"},
        "change_id": "chg-create",
        "templates_root": templates,
    }
    payload.update(overrides)
    return writer.create_node(conn, **payload)  # type: ignore[arg-type]


def _counts(conn: sqlite3.Connection) -> tuple[int, int]:
    defs = conn.execute("SELECT COUNT(*) FROM node_defs").fetchone()[0]
    versions = conn.execute("SELECT COUNT(*) FROM node_prompt_versions").fetchone()[0]
    return int(defs), int(versions)


def test_create_node_order_and_defaults(conn: sqlite3.Connection, templates: Path) -> None:
    conn.execute("CREATE TABLE write_log (id INTEGER PRIMARY KEY AUTOINCREMENT, step TEXT)")
    conn.execute(
        """
        CREATE TRIGGER log_def AFTER INSERT ON node_defs
        BEGIN
            INSERT INTO write_log(step) VALUES (
                'def:' || COALESCE(CAST(NEW.active_version AS TEXT), 'null')
            );
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER log_ver AFTER INSERT ON node_prompt_versions
        BEGIN
            INSERT INTO write_log(step) VALUES ('ver:' || NEW.kind);
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER log_ptr AFTER UPDATE ON node_defs
        BEGIN
            INSERT INTO write_log(step) VALUES (
                'ptr:' || COALESCE(CAST(NEW.active_version AS TEXT), 'null')
            );
        END
        """
    )
    version = writer.create_node(
        conn,
        node_key="n_qc_vo",
        group_key="excel_gpt",
        title="QC",
        prompt_template="привет",
        change_id="chg-1",
        created_via="orchestrator",
        templates_root=templates,
    )
    assert version == 1
    steps = [row[0] for row in conn.execute("SELECT step FROM write_log ORDER BY id")]
    assert steps == ["def:null", "ver:create", "ptr:1"]
    row = conn.execute(
        "SELECT origin, active_version, created_by FROM node_defs WHERE node_key = 'n_qc_vo'"
    ).fetchone()
    assert row[0] == "db"
    assert row[1] == 1
    stored = conn.execute(
        """
        SELECT prompt_template, params_json, kind, created_via, base_git_sha256
        FROM node_prompt_versions
        """
    ).fetchone()
    assert stored[0] == "привет"
    assert stored[1] == '{"model":"gpt-5.6-sol","reasoning_effort":"high"}'
    assert stored[2] == "create"
    assert stored[3] == "orchestrator"
    assert stored[4] is None
    node = resolve_node("n_qc_vo", conn, templates_root=templates)
    assert node.source == "db"
    assert node.db_version == 1


def test_edit_prompt_replace_section_and_ui_version(
    conn: sqlite3.Connection, templates: Path
) -> None:
    _git(templates)
    assert _create(conn, templates) == 1
    replaced = writer.edit_prompt(
        conn,
        node_key="n_qc_vo",
        content="целиком",
        patch_mode="replace",
        change_id="chg-2",
        reason="полная замена",
        created_via="studio_ui",
        templates_root=templates,
    )
    assert replaced == 2
    via = conn.execute(
        "SELECT created_via, patch_mode, prompt_template FROM node_prompt_versions WHERE version = 2"
    ).fetchone()
    assert tuple(via) == ("studio_ui", "replace", "целиком")
    original = conn.execute(
        "SELECT prompt_template FROM node_prompt_versions WHERE version = 1"
    ).fetchone()
    assert original[0] == "версия один"
    with pytest.raises(ValueError, match="section_anchor не найден"):
        writer.edit_prompt(
            conn,
            node_key="n_qc_vo",
            content="ещё",
            patch_mode="replace_section",
            section_anchor="Камера",
            change_id="chg-miss",
            reason="нет секции",
            templates_root=templates,
        )
    assert _counts(conn) == (1, 2)

    writer.edit_prompt(
        conn,
        node_key="n_qc_vo",
        content=_GIT,
        patch_mode="replace_full",
        change_id="chg-4",
        reason="вернуть секции",
        templates_root=templates,
    )
    appended = writer.edit_prompt(
        conn,
        node_key="n_qc_vo",
        content="ещё строка",
        patch_mode="append_section",
        section_anchor="## Камера",
        change_id="chg-5",
        reason="дописать",
        templates_root=templates,
    )
    assert appended == 4
    prompt = conn.execute(
        "SELECT prompt_template FROM node_prompt_versions WHERE version = 4"
    ).fetchone()[0]
    assert "## Камера\nстарый текст\nещё\n\nещё строка\n" in prompt
    assert "## Действие\nне трогать\n" in prompt
    node = resolve_node("n_qc_vo", conn, templates_root=templates)
    assert node.version == 4
    assert node.db_version == 4


def test_edit_section_on_git_template_creates_override(
    conn: sqlite3.Connection, templates: Path
) -> None:
    _git(templates)
    version = writer.edit_prompt(
        conn,
        node_key="n_qc_vo",
        content="новый",
        patch_mode="section",
        section_anchor="Камера",
        change_id="chg-1",
        reason="первая правка",
        created_via="studio_ui",
        templates_root=templates,
    )
    assert version == 1
    meta = conn.execute(
        "SELECT origin, group_key, title, active_version FROM node_defs"
    ).fetchone()
    assert tuple(meta) == ("git_override", "excel_gpt", "n_qc_vo", 1)
    prompt = conn.execute("SELECT prompt_template FROM node_prompt_versions").fetchone()[0]
    assert prompt == (
        "# Вход\n\n## Камера\nновый\n## Действие\nне трогать\n"
    )
    node = resolve_node("n_qc_vo", conn, templates_root=templates)
    assert node.source == "db"
    assert node.prompt_template == prompt


def test_edit_params_merges_full_snapshot(conn: sqlite3.Connection, templates: Path) -> None:
    _create(conn, templates)
    version = writer.edit_params(
        conn,
        node_key="n_qc_vo",
        params_patch={"temperature": 0.2},
        change_id="chg-params",
        reason="температура",
        created_via="studio_ui",
        templates_root=templates,
    )
    assert version == 2
    row = conn.execute(
        """
        SELECT prompt_template, params_json, kind, parent_version, created_via
        FROM node_prompt_versions WHERE version = 2
        """
    ).fetchone()
    assert row[0] == "версия один"
    assert row[1] == '{"model":"gpt-5.6-sol","reasoning_effort":"high","temperature":0.2}'
    assert row[2] == "edit_params"
    assert row[3] == 1
    assert row[4] == "studio_ui"
    untouched = conn.execute(
        "SELECT params_json FROM node_prompt_versions WHERE version = 1"
    ).fetchone()
    assert "temperature" not in untouched[0]


def test_rollback_copies_version_into_new_row(conn: sqlite3.Connection, templates: Path) -> None:
    _create(conn, templates, prompt_template="первый", params={"model": "a"})
    writer.edit_prompt(
        conn,
        node_key="n_qc_vo",
        content="второй",
        patch_mode="replace",
        change_id="chg-2",
        reason="правка",
        templates_root=templates,
    )
    rolled = writer.rollback_to_version(
        conn,
        node_key="n_qc_vo",
        version=1,
        change_id="chg-3",
        reason="откат",
        templates_root=templates,
    )
    assert rolled == 3
    rows = conn.execute(
        """
        SELECT version, kind, prompt_template, params_json, parent_version
        FROM node_prompt_versions ORDER BY version
        """
    ).fetchall()
    assert [tuple(row) for row in rows] == [
        (1, "create", "первый", '{"model":"a"}', None),
        (2, "edit_prompt", "второй", '{"model":"a"}', 1),
        (3, "rollback", "первый", '{"model":"a"}', 1),
    ]
    assert conn.execute("SELECT active_version FROM node_defs").fetchone()[0] == 3
    node = resolve_node("n_qc_vo", conn, templates_root=templates)
    assert node.prompt_template == "первый"
    assert node.db_version == 3


def test_revert_returns_to_git_and_blocks_pointer(
    conn: sqlite3.Connection, templates: Path
) -> None:
    _git(templates, "git-текст\n")
    _create(conn, templates, prompt_template="db-текст")
    writer.edit_prompt(
        conn,
        node_key="n_qc_vo",
        content="db-два",
        patch_mode="replace",
        change_id="chg-2",
        reason="ещё",
        templates_root=templates,
    )
    reverted = writer.revert_node_to_git(
        conn,
        node_key="n_qc_vo",
        change_id="chg-revert",
        reason="назад к git",
        created_via="studio_ui",
        templates_root=templates,
    )
    assert reverted == 3
    kind, via, prompt = conn.execute(
        """
        SELECT kind, created_via, prompt_template
        FROM node_prompt_versions WHERE version = 3
        """
    ).fetchone()
    assert kind == "revert_to_git"
    assert via == "studio_ui"
    assert prompt == "git-текст\n"
    assert conn.execute("SELECT active_version FROM node_defs").fetchone()[0] is None
    node = resolve_node("n_qc_vo", conn, templates_root=templates)
    assert node.source == "git"
    assert node.prompt_template == "git-текст\n"
    assert node.db_version is None
    assert node.version == 0
    assert node.stale_base is False
    with pytest.raises(sqlite3.IntegrityError, match="non-revert"):
        conn.execute("UPDATE node_defs SET active_version = 3 WHERE node_key = 'n_qc_vo'")
    with pytest.raises(NodeStoreError, match="уже на git"):
        writer.revert_node_to_git(
            conn,
            node_key="n_qc_vo",
            change_id="chg-again",
            reason="повтор",
            templates_root=templates,
        )
    assert _counts(conn) == (1, 3)
    nxt = writer.edit_prompt(
        conn,
        node_key="n_qc_vo",
        content="снова из ui",
        patch_mode="replace",
        change_id="chg-4",
        reason="после revert",
        created_via="studio_ui",
        templates_root=templates,
    )
    assert nxt == 4
    assert resolve_node("n_qc_vo", conn, templates_root=templates).prompt_template == "снова из ui"


def test_failed_section_and_duplicate_leave_no_rows(
    conn: sqlite3.Connection, templates: Path
) -> None:
    _git(templates)
    with pytest.raises(ValueError, match="section_anchor не найден"):
        writer.edit_prompt(
            conn,
            node_key="n_qc_vo",
            content="x",
            patch_mode="section",
            section_anchor="Нет такой",
            change_id="chg-x",
            reason="мимо",
            templates_root=templates,
        )
    assert _counts(conn) == (0, 0)
    _create(conn, templates)
    with pytest.raises(NodeExistsError):
        _create(conn, templates, change_id="chg-dup")
    assert _counts(conn) == (1, 1)
    with pytest.raises(ValueError, match="created_via"):
        writer.edit_params(
            conn,
            node_key="n_qc_vo",
            params_patch={"model": "x"},
            change_id="chg-bad",
            reason="мимо",
            created_via="browser",
            templates_root=templates,
        )
    assert _counts(conn) == (1, 1)


def test_atomicity_on_pointer_failure(
    conn: sqlite3.Connection, templates: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("mid-transaction")

    monkeypatch.setattr(writer, "_set_active_version", boom)
    with pytest.raises(RuntimeError, match="mid-transaction"):
        _create(conn, templates)
    assert _counts(conn) == (0, 0)
    monkeypatch.undo()
    assert _create(conn, templates) == 1


def test_atomicity_when_update_trigger_aborts(conn: sqlite3.Connection, templates: Path) -> None:
    conn.execute(
        """
        CREATE TRIGGER node_store_test_fail_pointer
        BEFORE UPDATE ON node_defs
        WHEN NEW.active_version IS NOT NULL
        BEGIN
            SELECT RAISE(ABORT, 'boom-mid');
        END
        """
    )
    with pytest.raises(sqlite3.IntegrityError, match="boom-mid"):
        _create(conn, templates)
    assert _counts(conn) == (0, 0)
    conn.execute("DROP TRIGGER node_store_test_fail_pointer")
    assert _create(conn, templates) == 1
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE node_prompt_versions SET reason = 'x'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("DELETE FROM node_prompt_versions")
    assert conn.execute("SELECT prompt_template FROM node_prompt_versions").fetchone()[0] == "версия один"


def test_missing_node_and_bad_key(conn: sqlite3.Connection, templates: Path) -> None:
    with pytest.raises(NodeNotFoundError):
        writer.edit_params(
            conn,
            node_key="n_absent",
            params_patch={"model": "m"},
            change_id="chg",
            reason="нет",
            templates_root=templates,
        )
    with pytest.raises(ValueError, match="node_key"):
        writer.create_node(
            conn,
            node_key="Bad",
            group_key="excel_gpt",
            title="t",
            prompt_template="p",
            change_id="chg",
            templates_root=templates,
        )
    assert _counts(conn) == (0, 0)
