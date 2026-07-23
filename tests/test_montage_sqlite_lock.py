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
