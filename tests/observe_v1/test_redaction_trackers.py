from __future__ import annotations

import sqlite3
import time
from unittest.mock import patch

import pytest

from app.services import api_tracker_hook as hook
from app.settings import settings

SECRET = "sk-ABCDEFGH1234567890zz"


@pytest.fixture
def tracker(tmp_path, monkeypatch):
    db = tmp_path / "tracker.db"
    monkeypatch.setattr(hook, "_TRACKER_DB", db)
    monkeypatch.setattr(hook, "_get_identity", lambda: ("tester", "dev"))
    pushed: list = []

    def fake_push(payload):
        pushed.append(payload)
        return True

    monkeypatch.setattr(hook, "_push_to_supabase", fake_push)
    return db, pushed


def _wait_rows(db, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with sqlite3.connect(str(db)) as c:
                rows = c.execute("SELECT error_message, synced, project_source FROM api_calls").fetchall()
            if rows:
                return rows
        except sqlite3.Error:
            pass
        time.sleep(0.05)
    raise AssertionError("tracker row was not written")


def test_error_message_redacted_locally_and_in_cloud(tracker, monkeypatch):
    db, pushed = tracker
    monkeypatch.setattr(settings, "api_tracker_cloud_enabled", True)
    hook.record_api_call(provider="kie", model="m", status_code=500,
                         error_message=f"HTTPError: Bearer {SECRET} key={SECRET}")
    rows = _wait_rows(db)
    assert SECRET not in rows[0][0]
    assert len(pushed) == 1 and SECRET not in pushed[0]["error_message"]


def test_cloud_flag_off_skips_push_and_marks_nothing_pending(tracker, monkeypatch):
    db, pushed = tracker
    monkeypatch.setattr(settings, "api_tracker_cloud_enabled", False)
    hook.record_api_call(provider="kie", model="m", error_message="boom")
    rows = _wait_rows(db)
    assert pushed == []
    assert rows[0][1] == 1


def test_cloud_flag_default_is_off_and_nothing_is_pushed(tracker):
    assert settings.api_tracker_cloud_enabled is False
    db, pushed = tracker
    hook.record_api_call(provider="kie", model="m", error_message="boom")
    _wait_rows(db)
    assert pushed == []


def test_track_api_cost_redacts_before_record(monkeypatch):
    from app.services import observation
    monkeypatch.setattr(settings, "gpt_api_key", "real-key-for-test")
    with patch("app.services.api_tracker_hook.record_api_call") as rec:
        observation._track_api_cost(model="m", usage=None, duration_sec=0.1, status_code=0,
                                    error_message=f"ConnectError: https://x/?api_key={SECRET}")
    assert rec.call_count == 1
    assert SECRET not in rec.call_args.kwargs["error_message"]
