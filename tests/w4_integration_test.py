"""W4 интеграция: общие хелперы, gpt_truncated, русские тексты диагностики, регистрация уведомителя.

Только локальный код, без сети и платных вызовов."""
from __future__ import annotations

import os

from app.db import is_sqlite_locked
from app.services import diagnosis
from app.services.error_catalog import describe_error


def test_conftest_switches_auto_observation_off_by_default():
    from app.services.observation import auto_observe_enabled
    assert os.environ.get("OBSERVER_AUTO_ENABLE") == "0"
    assert auto_observe_enabled(None) is False


def test_sqlite_locked_helper_is_shared():
    from app.services import audio_align_job, audio_align_run, montage_board_apply
    assert audio_align_job._is_sqlite_locked is is_sqlite_locked
    assert audio_align_run._is_sqlite_locked is is_sqlite_locked
    assert montage_board_apply._is_sqlite_locked is is_sqlite_locked
    assert is_sqlite_locked(RuntimeError("database is locked"))
    assert not is_sqlite_locked(RuntimeError("no such table"))


def test_shot_video_finders_have_one_implementation():
    from app.services import montage_board, montage_board_assets
    assert montage_board._find_shot1_video is montage_board_assets._find_shot1_video
    assert montage_board._find_shot2_video is montage_board_assets._find_shot2_video


def test_truncated_error_has_its_own_russian_code():
    from app.services.gpt_api import GptTruncatedError
    exc = GptTruncatedError("усечён", context={"error_kind": "truncated"})
    code, text = describe_error(exc)
    assert code == "gpt_truncated" and "обрезан" in text
    from app.services.error_catalog import _match_code
    assert _match_code(exc) == "gpt_truncated"


def test_decision_uses_actions_recovery_classes():
    from app.services import actions_recovery, decision_v1
    assert decision_v1.DIAGNOSTIC_INCIDENTS == set(actions_recovery.RECOVERABLE_INCIDENT_TYPES)
    assert decision_v1.RECOVERY_INCIDENTS == set(actions_recovery.FORMAT_INCIDENTS)
    # media never joins the automatic classes
    assert "media_failure" not in decision_v1.DIAGNOSTIC_INCIDENTS
    assert actions_recovery.recovery_class("media_failure", {}) is None
    assert actions_recovery.recovery_class("api_error", {"safe_retry": {"eligible": False}}) is None


def test_escalation_notifier_registration_is_safe(monkeypatch):
    from app.settings import settings
    monkeypatch.setattr(diagnosis, "_escalation_notifier", None)
    monkeypatch.setattr(settings, "telegram_bot_token", "", raising=False)
    monkeypatch.setattr(type(settings), "telegram_active", property(lambda self: False), raising=False)
    assert diagnosis.register_default_escalation_notifier() is False
    assert diagnosis._escalation_notifier is None
    monkeypatch.setattr(type(settings), "telegram_active", property(lambda self: True), raising=False)
    assert diagnosis.register_default_escalation_notifier() is True
    assert diagnosis._escalation_notifier is not None
    assert diagnosis.register_default_escalation_notifier() is False  # already set: not replaced
