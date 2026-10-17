"""Схема SQLite совпадает со спецификациями WS2/WS5 в contracts.py."""

from __future__ import annotations

import sqlite3

import pytest

from app.orchestrator.contracts import (
    TABLE_SPECS,
    Chunk,
    ChunkCorpus,
    LLMResponse,
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
                is_active, change_id, created_at
            ) VALUES (
                'n_qc_vo', 1, 'prompt', '{"model":"gpt-5.6-sol"}', 'create',
                1, 'chg-text', ?
            )
            """,
            (stamp,),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO node_prompt_versions (
                    node_key, version, prompt_template, params_json, kind,
                    is_active, change_id, created_at
                ) VALUES (
                    'n_qc_vo', 2, 'other', '{}', 'edit_prompt',
                    1, 'chg-2', ?
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
