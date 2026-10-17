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
    assert _create(conn, templates, origin="git_override") == 1
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
    _create(conn, templates, prompt_template="db-текст", origin="git_override")
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
    _create(conn, templates, origin="git_override")
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


def test_revert_without_git_and_only_for_git_override(
    conn: sqlite3.Connection, templates: Path
) -> None:
    _create(conn, templates)
    with pytest.raises(NodeNotFoundError, match="нечего восстанавливать из git"):
        writer.revert_node_to_git(
            conn,
            node_key="n_qc_vo",
            change_id="chg-revert",
            reason="некуда",
            templates_root=templates,
        )
    assert _counts(conn) == (1, 1)
    assert conn.execute("SELECT active_version FROM node_defs").fetchone()[0] == 1
    _git(templates, "git-текст\n")
    with pytest.raises(NodeStoreError, match="git_override"):
        writer.revert_node_to_git(
            conn,
            node_key="n_qc_vo",
            change_id="chg-revert-2",
            reason="origin db",
            templates_root=templates,
        )
    assert _counts(conn) == (1, 1)


def test_create_over_git_requires_explicit_origin(conn: sqlite3.Connection, templates: Path) -> None:
    _git(templates, "git\n")
    with pytest.raises(NodeExistsError, match="git_override"):
        _create(conn, templates)
    assert _counts(conn) == (0, 0)
    with pytest.raises(ValueError, match="prompt_template пуст"):
        writer.create_node(
            conn,
            node_key="n_empty",
            group_key="excel_gpt",
            title="Пусто",
            prompt_template="   ",
            change_id="chg-empty",
            templates_root=templates,
        )
    assert _create(conn, templates, origin="git_override") == 1


def test_reasoning_effort_cannot_drop_below_high(conn: sqlite3.Connection, templates: Path) -> None:
    _create(conn, templates)
    with pytest.raises(NodeStoreError, match="reasoning_effort"):
        writer.edit_params(
            conn,
            node_key="n_qc_vo",
            params_patch={"reasoning_effort": "low"},
            change_id="chg-low",
            reason="понизить",
            templates_root=templates,
        )
    with pytest.raises(NodeStoreError, match="reasoning_effort"):
        writer.create_node(
            conn,
            node_key="n_low",
            group_key="excel_gpt",
            title="Low",
            prompt_template="промпт",
            params={"reasoning_effort": "low"},
            change_id="chg-low-create",
            templates_root=templates,
        )
    assert _counts(conn) == (1, 1)
    # Правило stagger/parallel снято: эти поля пишутся без проверки.
    version = writer.edit_params(
        conn,
        node_key="n_qc_vo",
        params_patch={"stagger_sec": 0, "parallel": 1},
        change_id="chg-stagger",
        reason="сдвиг снят",
        templates_root=templates,
    )
    assert version == 2


def test_rollback_rejects_revert_row(conn: sqlite3.Connection, templates: Path) -> None:
    _git(templates, "git-текст\n")
    _create(conn, templates, origin="git_override")
    reverted = writer.revert_node_to_git(
        conn,
        node_key="n_qc_vo",
        change_id="chg-revert",
        reason="на git",
        templates_root=templates,
    )
    with pytest.raises(NodeStoreError, match="revert_to_git"):
        writer.rollback_to_version(
            conn,
            node_key="n_qc_vo",
            version=reverted,
            change_id="chg-roll",
            reason="обойти revert",
            templates_root=templates,
        )
    assert conn.execute("SELECT COUNT(*) FROM node_prompt_versions").fetchone()[0] == 2
    assert conn.execute("SELECT active_version FROM node_defs").fetchone()[0] is None


def test_rule_anchor_section(conn: sqlite3.Connection, templates: Path) -> None:
    prompt = "intro\n   R-NO-OVERSPLIT. старое\n   продолжение\n   R-OTHER. не трогать\n"
    _git(templates, prompt)
    version = writer.edit_prompt(
        conn,
        node_key="n_qc_vo",
        content="   R-NO-OVERSPLIT. новое",
        patch_mode="section",
        section_anchor="R-NO-OVERSPLIT",
        change_id="chg-rule",
        reason="правило",
        templates_root=templates,
    )
    assert version == 1
    stored = conn.execute("SELECT prompt_template FROM node_prompt_versions").fetchone()[0]
    assert stored == "intro\n   R-NO-OVERSPLIT. новое\n   R-OTHER. не трогать\n"
    with pytest.raises(ValueError, match="section_anchor не найден"):
        writer.edit_prompt(
            conn,
            node_key="n_qc_vo",
            content="x",
            patch_mode="section",
            section_anchor="старое",
            change_id="chg-body",
            reason="тело не якорь",
            templates_root=templates,
        )


def test_materialize_group_and_title(conn: sqlite3.Connection, templates: Path) -> None:
    from app.orchestrator.node_store.git_baseline import GitBaseline
    from app.orchestrator.node_store.writer import _materialize_group, _materialize_title, _State

    empty = _State(
        exists=False,
        active_version=None,
        prompt="",
        params={},
        params_json="{}",
        baseline=GitBaseline(
            path=None,
            prompt_template="",
            params={},
            sha256=None,
            group_key=None,
            title=None,
        ),
    )
    with pytest.raises(ValueError, match="group_key"):
        _materialize_group(empty, None)
    with pytest.raises(ValueError, match="title"):
        _materialize_title(empty, None)
    assert _materialize_group(empty, "excel_gpt") == "excel_gpt"
    assert _materialize_title(empty, "QC заголовок") == "QC заголовок"

    (templates / "n_root.md").write_text("# only\n", encoding="utf-8")
    with pytest.raises(ValueError, match="group_key"):
        writer.edit_prompt(
            conn,
            node_key="n_root",
            content="новый",
            patch_mode="replace",
            change_id="chg-root",
            reason="без группы",
            templates_root=templates,
        )
    assert _counts(conn) == (0, 0)
    version = writer.edit_prompt(
        conn,
        node_key="n_root",
        content="новый",
        patch_mode="replace",
        change_id="chg-root-2",
        reason="с группой",
        group_key="excel_gpt",
        templates_root=templates,
    )
    assert version == 1
    meta = conn.execute("SELECT group_key, title, origin FROM node_defs").fetchone()
    assert tuple(meta) == ("excel_gpt", "n_root", "git_override")


def test_nested_atomic_savepoint(conn: sqlite3.Connection) -> None:
    from app.orchestrator.node_store._db import atomic

    stamp = "2026-10-17T00:00:00.000000Z"
    with atomic(conn):
        conn.execute(
            """
            INSERT INTO node_defs (
                node_key, group_key, title, origin, is_new_group, status,
                active_version, created_at, updated_at
            ) VALUES ('n_qc_vo', 'excel_gpt', 'QC', 'db', 0, 'active', NULL, ?, ?)
            """,
            (stamp, stamp),
        )
        with pytest.raises(RuntimeError, match="inner"), atomic(conn):
            conn.execute(
                """
                    INSERT INTO node_prompt_versions (
                        node_key, version, prompt_template, params_json, kind,
                        change_id, created_at
                    ) VALUES ('n_qc_vo', 1, 'p', '{}', 'create', 'chg', ?)
                    """,
                (stamp,),
            )
            raise RuntimeError("inner")
        assert _counts(conn) == (1, 0)
    assert _counts(conn) == (1, 0)


def test_atomicity_autocommit(
    autocommit_conn: sqlite3.Connection, templates: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("mid-transaction")

    monkeypatch.setattr(writer, "_set_active_version", boom)
    with pytest.raises(RuntimeError, match="mid-transaction"):
        _create(autocommit_conn, templates)
    assert _counts(autocommit_conn) == (0, 0)
    monkeypatch.undo()
    assert _create(autocommit_conn, templates) == 1


def test_unique_version_retries(
    conn: sqlite3.Connection, templates: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _create(conn, templates)
    real = writer._insert_version_row
    calls = {"n": 0}

    def flaky(*args: object, **kwargs: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.IntegrityError(
                "UNIQUE constraint failed: node_prompt_versions.node_key, node_prompt_versions.version"
            )
        real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(writer, "_insert_version_row", flaky)
    version = writer.edit_prompt(
        conn,
        node_key="n_qc_vo",
        content="после коллизии",
        patch_mode="replace",
        change_id="chg-retry",
        reason="retry",
        templates_root=templates,
    )
    assert version == 2
    assert calls["n"] == 2
    assert conn.execute("SELECT COUNT(*) FROM node_prompt_versions").fetchone()[0] == 2


def test_two_writers(tmp_path: Path, templates: Path) -> None:
    import threading

    db_path = tmp_path / "writers.db"
    schema = (Path(__file__).parent / "schema_346.sql").read_text(encoding="utf-8")

    def open_conn() -> sqlite3.Connection:
        connection = sqlite3.connect(db_path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.create_function(
            "regexp",
            2,
            lambda pattern, value: 0 if value is None else int(__import__("re").search(pattern, str(value)) is not None),
        )
        return connection

    setup = open_conn()
    setup.executescript(schema)
    _create(setup, templates)
    setup.close()

    versions: list[int] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        connection = open_conn()
        try:
            version = writer.edit_prompt(
                connection,
                node_key="n_qc_vo",
                content=f"писатель {index}",
                patch_mode="replace",
                change_id=f"chg-w{index}",
                reason="два писателя",
                templates_root=templates,
            )
        except BaseException as exc:  # noqa: BLE001 — тест собирает ошибки потока
            with lock:
                errors.append(exc)
        else:
            with lock:
                versions.append(version)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(index,)) for index in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert sorted(versions) == [2, 3]
    check = open_conn()
    try:
        rows = check.execute(
            "SELECT version, prompt_template FROM node_prompt_versions ORDER BY version"
        ).fetchall()
        assert [row[0] for row in rows] == [1, 2, 3]
        assert {row[1] for row in rows[1:]} == {"писатель 1", "писатель 2"}
    finally:
        check.close()


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
