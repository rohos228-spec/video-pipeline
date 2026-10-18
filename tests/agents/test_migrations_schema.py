"""Схема SQLite совпадает со спецификациями WS2/WS5 в contracts.py."""

from __future__ import annotations

import sqlite3

import pytest

from app.orchestrator.contracts import (
    TABLE_SPECS,
    Chunk,
    ChunkCorpus,
    LLMResponse,
    NodeDef,
    NodeSource,
    TaskStatus,
    export_contract_schemas,
)
from app.orchestrator.db.migrations import apply_migrations, connect, utc_now


def test_tables_match_contract_columns() -> None:
    conn = connect()
    try:
        assert apply_migrations(conn) == []
        for spec in TABLE_SPECS:
            rows = conn.execute(f"PRAGMA table_info({spec.name})").fetchall()
            assert [row[1] for row in rows] == [column.name for column in spec.columns]
            for row, column in zip(rows, spec.columns, strict=True):
                assert row[2].upper() == column.sql_type.upper()
    finally:
        conn.close()


def test_node_and_report_constraints() -> None:
    conn = connect()
    stamp = utc_now()
    try:
        conn.execute(
            """
            INSERT INTO node_defs (
                node_key, group_key, title, origin, is_new_group, status,
                created_at, updated_at, created_by
            ) VALUES ('n_qc_vo', 'excel_gpt', 'QC', 'db', 0, 'active', ?, ?, 'task-1')
            """,
            (stamp, stamp),
        )
        conn.execute(
            """
            INSERT INTO node_prompt_versions (
                node_key, version, prompt_template, params_json, kind,
                change_id, created_at
            ) VALUES (
                'n_qc_vo', 1, 'prompt', '{"model":"gpt-5.6-sol"}', 'create',
                'chg-text', ?
            )
            """,
            (stamp,),
        )
        conn.execute(
            """
            INSERT INTO node_prompt_versions (
                node_key, version, prompt_template, params_json, kind,
                change_id, created_at
            ) VALUES (
                'n_qc_vo', 2, 'other', '{}', 'edit_prompt',
                'chg-2', ?
            )
            """,
            (stamp,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO node_prompt_versions (
                    node_key, version, prompt_template, params_json, kind,
                    change_id, created_at
                ) VALUES (
                    'n_qc_vo', 2, 'dup', '{}', 'edit_prompt',
                    'chg-dup', ?
                )
                """,
                (stamp,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("UPDATE node_prompt_versions SET reason = 'x' WHERE version = 1")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM node_prompt_versions WHERE version = 1")
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
        conn.execute(
            """
            INSERT INTO orch_reports (
                project_id, slug, title, editable, version, kind, created_at, updated_at
            ) VALUES (7, 'qc', 'QC', 1, 1, 'report', ?, ?)
            """,
            (stamp, stamp),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO orch_reports (
                    project_id, slug, title, editable, version, kind, created_at, updated_at
                ) VALUES (7, 'qc', 'dup', 1, 1, 'nodes_snapshot', ?, ?)
                """,
                (stamp, stamp),
            )
        conn.commit()
    finally:
        conn.close()


def test_llm_calls_follow_ws5_and_skip_prompt_text() -> None:
    conn = connect()
    stamp = utc_now()
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(orch_llm_calls)")]
        assert "prompt" not in columns
        assert "response" not in columns
        assert "messages" not in columns
        conn.execute(
            """
            INSERT INTO orch_llm_calls (
                call_id, task_id, run_id, agent_role, requested_tier, tier,
                model, provider, reasoning_effort, fallback_index, attempt, status,
                error, deterministic_flag, classifier_confidence, latency_ms,
                prompt_tokens, completion_tokens, reasoning_tokens, cost_usd,
                budget_scope, router_impl, messages_sha256, fixture_key, created_at
            ) VALUES (
                '8b2c1d44-5e6f-4789-a012-3456789abcde', '15', NULL, 'classifier',
                'auto', 'fast', 'glm-5.3-flash', '52pay', NULL, 0, 1, 'ok',
                NULL, 1, 0.91, 120, 10, 4, 0, 0.001,
                'internal', 'scripted', 'abc', NULL, ?
            )
            """,
            (stamp,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO orch_llm_calls (
                    call_id, agent_role, requested_tier, tier, model, provider,
                    fallback_index, attempt, status, deterministic_flag, latency_ms,
                    prompt_tokens, completion_tokens, reasoning_tokens, cost_usd,
                    budget_scope, router_impl, created_at
                ) VALUES (
                    '8b2c1d44-5e6f-4789-a012-3456789abcde', 'planner', 'strong', 'strong',
                    'm', 'kie', 0, 1, 'ok', 0, 1, 1, 1, 1, 0, 'internal', 'live', ?
                )
                """,
                (stamp,),
            )
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(orch_llm_calls)")}
        assert "ix_orch_llm_calls_task_created" in indexes
        assert "ix_orch_llm_calls_budget_scope" in indexes
    finally:
        conn.close()


def test_node_def_is_the_resolved_shape() -> None:
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
    node = NodeDef(
        node_key="n_qc_vo",
        prompt_template="prompt",
        params={"model": "scripted"},
        source=NodeSource.db,
        version=2,
        db_version=2,
        git_sha256="abc",
        stale_base=True,
    )
    dumped = node.model_dump()
    assert "overridden_in_db" not in dumped
    assert "active_version" not in dumped
    assert "group_key" not in dumped
    statuses = {item.value for item in TaskStatus}
    assert "escalated" not in statuses
    assert "budget_exhausted" not in statuses
    schema = export_contract_schemas()["models"]["NodeDef"]["properties"]
    assert set(schema) == set(NodeDef.model_fields)


def test_revert_to_git_and_studio_ui_columns() -> None:
    conn = connect()
    stamp = utc_now()
    try:
        conn.execute(
            """
            INSERT INTO node_defs (
                node_key, group_key, title, origin, is_new_group, status,
                active_version, created_at, updated_at, created_by
            ) VALUES (
                'n_qc_vo', 'excel_gpt', 'QC', 'git_override', 0, 'active',
                NULL, ?, ?, 'task-1'
            )
            """,
            (stamp, stamp),
        )
        conn.execute(
            """
            INSERT INTO node_prompt_versions (
                node_key, version, prompt_template, params_json, kind,
                parent_version, base_git_sha256, change_id,
                created_at, created_by, created_via
            ) VALUES (
                'n_qc_vo', 1, 'prompt', '{}', 'revert_to_git',
                NULL, 'deadbeef', 'chg-revert',
                ?, 'studio-user', 'studio_ui'
            )
            """,
            (stamp,),
        )
        row = conn.execute(
            """
            SELECT kind, base_git_sha256, created_via
            FROM node_prompt_versions WHERE version = 1
            """
        ).fetchone()
        assert tuple(row) == ("revert_to_git", "deadbeef", "studio_ui")
        columns = [info[1] for info in conn.execute("PRAGMA table_info(node_prompt_versions)")]
        assert "is_active" not in columns
        indexes = {info[1] for info in conn.execute("PRAGMA index_list(node_prompt_versions)")}
        assert "ux_node_prompt_versions_one_active" not in indexes
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO node_prompt_versions (
                    node_key, version, prompt_template, params_json, kind,
                    change_id, created_at, created_via
                ) VALUES (
                    'n_qc_vo', 2, 'prompt', '{}', 'ui_edit',
                    'chg-ui', ?, 'studio_ui'
                )
                """,
                (stamp,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO node_prompt_versions (
                    node_key, version, prompt_template, params_json, kind,
                    change_id, created_at, created_via
                ) VALUES (
                    'n_qc_vo', 3, 'prompt', '{}', 'edit_prompt',
                    'chg-via', ?, 'notepad'
                )
                """,
                (stamp,),
            )
        conn.commit()
    finally:
        conn.close()


def test_active_version_points_at_existing_non_revert() -> None:
    conn = connect()
    stamp = utc_now()
    try:
        conn.execute(
            """
            INSERT INTO node_defs (
                node_key, group_key, title, origin, is_new_group, status,
                created_at, updated_at
            ) VALUES ('n_qc_vo', 'g', 'QC', 'db', 0, 'active', ?, ?)
            """,
            (stamp, stamp),
        )
        conn.execute(
            """
            INSERT INTO node_defs (
                node_key, group_key, title, origin, is_new_group, status,
                created_at, updated_at
            ) VALUES ('n_other', 'g', 'Other', 'db', 0, 'active', ?, ?)
            """,
            (stamp, stamp),
        )
        conn.execute(
            """
            INSERT INTO node_prompt_versions (
                node_key, version, prompt_template, params_json, kind, change_id, created_at
            ) VALUES ('n_qc_vo', 1, 'prompt', '{}', 'create', 'chg-1', ?)
            """,
            (stamp,),
        )
        conn.execute(
            """
            INSERT INTO node_prompt_versions (
                node_key, version, prompt_template, params_json, kind, change_id, created_at
            ) VALUES ('n_qc_vo', 2, 'back', '{}', 'revert_to_git', 'chg-2', ?)
            """,
            (stamp,),
        )
        conn.execute(
            """
            INSERT INTO node_prompt_versions (
                node_key, version, prompt_template, params_json, kind, change_id, created_at
            ) VALUES ('n_other', 1, 'other', '{}', 'create', 'chg-3', ?)
            """,
            (stamp,),
        )
        conn.execute(
            """
            INSERT INTO node_prompt_versions (
                node_key, version, prompt_template, params_json, kind, change_id, created_at
            ) VALUES ('n_other', 7, 'only-other', '{}', 'edit_prompt', 'chg-7', ?)
            """,
            (stamp,),
        )
        conn.execute("UPDATE node_defs SET active_version = 1 WHERE node_key = 'n_qc_vo'")
        conn.execute("UPDATE node_defs SET active_version = NULL WHERE node_key = 'n_qc_vo'")
        with pytest.raises(sqlite3.IntegrityError, match="non-revert"):
            conn.execute("UPDATE node_defs SET active_version = 2 WHERE node_key = 'n_qc_vo'")
        with pytest.raises(sqlite3.IntegrityError, match="non-revert"):
            conn.execute("UPDATE node_defs SET active_version = 9 WHERE node_key = 'n_qc_vo'")
        with pytest.raises(sqlite3.IntegrityError, match="non-revert"):
            conn.execute("UPDATE node_defs SET active_version = 7 WHERE node_key = 'n_qc_vo'")
        with pytest.raises(sqlite3.IntegrityError, match="non-revert"):
            conn.execute(
                """
                INSERT INTO node_defs (
                    node_key, group_key, title, origin, is_new_group, status,
                    active_version, created_at, updated_at
                ) VALUES ('n_fresh', 'g', 'F', 'db', 0, 'active', 1, ?, ?)
                """,
                (stamp, stamp),
            )
        pointed = conn.execute(
            "SELECT active_version FROM node_defs WHERE node_key = 'n_other'"
        ).fetchone()
        assert pointed[0] is None
        conn.execute("UPDATE node_defs SET active_version = 1 WHERE node_key = 'n_other'")
        assert conn.execute(
            "SELECT active_version FROM node_defs WHERE node_key = 'n_qc_vo'"
        ).fetchone()[0] is None
        conn.commit()
    finally:
        conn.close()


def test_export_schema_has_ws5_models() -> None:
    payload = export_contract_schemas()
    llm = payload["models"]["LLMResponse"]["properties"]
    assert "call_id" in llm
    assert "usage" in llm
    assert "cost_usd" in llm
    assert "fixture_key" in llm
    chunk = payload["models"]["Chunk"]["properties"]
    assert "chunk_id" in chunk
    assert "score_bm25" in chunk
    assert "metadata" in chunk
    assert payload["enums"]["ChunkCorpus"] == [item.value for item in ChunkCorpus]
    sample = LLMResponse(text="ok", call_id="c", model="m", provider="52pay", tier="fast")
    assert sample.fixture_key is None
    Chunk(
        chunk_id="sha",
        corpus=ChunkCorpus.nodes,
        text="t",
        metadata={"node_key": "n_qc_vo", "version": 1},
    )


def test_node_key_check_works_on_bare_sqlite() -> None:
    """CHECK на GLOB, без Python-функции regexp. Голое sqlite3.connect вставляет строку."""
    owned = connect()
    path = owned.execute("PRAGMA database_list").fetchone()[2]
    owned.close()
    bare = sqlite3.connect(path)
    stamp = utc_now()
    try:
        bare.execute(
            """
            INSERT INTO node_defs (
                node_key, group_key, title, origin, is_new_group, status,
                created_at, updated_at
            ) VALUES ('n_qc_vo', 'g', 'QC', 'db', 0, 'active', ?, ?)
            """,
            (stamp, stamp),
        )
        bare.commit()
        for bad in ("Foo", "n_", "n_Bad", "n_a-b"):
            with pytest.raises(sqlite3.IntegrityError):
                bare.execute(
                    """
                    INSERT INTO node_defs (
                        node_key, group_key, title, origin, is_new_group, status,
                        created_at, updated_at
                    ) VALUES (?, 'g', 't', 'db', 0, 'active', ?, ?)
                    """,
                    (bad, stamp, stamp),
                )
    finally:
        bare.close()
