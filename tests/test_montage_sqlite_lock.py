"""Montage apply не держит SQLite write-txn на весь Outsee Generate."""

from __future__ import annotations

import inspect

from app.services import montage_board_apply as apply_mod


def test_apply_commits_after_each_op() -> None:
    src = inspect.getsource(apply_mod.apply_montage_board)
    assert "await session.commit()" in src
    assert src.count("await session.commit()") >= 2


def test_finalize_retries_on_sqlite_lock() -> None:
    assert "_finalize_image_with_retry" in dir(apply_mod)
    src = inspect.getsource(apply_mod._finalize_image_with_retry)
    assert "database is locked" in src or "_is_sqlite_locked" in src
    assert "range(1, 8)" in src


def test_db_busy_timeout_at_least_60s() -> None:
    from app import db as db_mod

    src = inspect.getsource(db_mod._configure_sqlite_connection)
    assert "busy_timeout=60000" in src
    assert '"timeout": 60' in inspect.getsource(db_mod) or "timeout\": 60" in inspect.getsource(
        db_mod
    )
    engine_src = inspect.getsource(db_mod)
    assert "NullPool" in engine_src
    assert "synchronous=NORMAL" in src


def test_canvas_topology_key_ignores_position() -> None:
    from app.services.canvas_graph import canvas_topology_key

    a = [
        {"id": "n1", "type": "plan", "position": {"x": 0, "y": 0}},
        {"id": "n2", "type": "script", "position": {"x": 100, "y": 0}},
    ]
    b = [
        {"id": "n1", "type": "plan", "position": {"x": 50, "y": 80}},
        {"id": "n2", "type": "script", "position": {"x": 200, "y": 10}},
    ]
    edges = [{"source": "n1", "target": "n2", "data": {"kind": "after"}}]
    assert canvas_topology_key(a, edges) == canvas_topology_key(b, edges)
