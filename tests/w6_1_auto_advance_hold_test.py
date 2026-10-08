"""W6-1 (E01/E02): переход assembled -> publishing в auto_advance. Пока публикация пропущена, ждёт подтверждения
человека или его проверки, проект сам в publishing не возвращается; auto_mode и HITL_AUTO_APPROVE этого не меняют.

Без сети и провайдеров (по образцу w4_1_auto_approve_unchecked_test). Класс доказательства: U.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.models import HITLDecision, HITLKind, HITLRequest, Project, ProjectStatus
from app.orchestrator import auto_advance as aa
from app.settings import settings


class _Sess:
    async def refresh(self, *_a, **_k):
        return None

    async def flush(self):
        return None


def _drive(monkeypatch, meta, *, final_uuid="u1", hitl_auto=False):
    project = Project(slug="hold", topic="t", status=ProjectStatus.assembled, auto_mode=True, meta=meta)
    project.id = 61
    hitl = HITLRequest(project_id=61, kind=HITLKind.approve_final, decision=HITLDecision.pending)
    hitl.payload = {}
    hitl.tg_message_id = None
    apply_mock = AsyncMock(return_value=None)
    get_hitl = AsyncMock(return_value=hitl)
    monkeypatch.setattr(settings, "hitl_auto_approve", hitl_auto)
    monkeypatch.setattr("app.services.gen_queue_run.is_user_stopped", lambda _p: False)
    monkeypatch.setattr("app.services.project_control.auto_awaits_manual_start", lambda _p: False)
    monkeypatch.setattr("app.services.gen_queue.project_gated_by_gen_queue", lambda _id: False)
    monkeypatch.setattr("app.services.gen_queue.gen_queue_blocks_project", AsyncMock(return_value=None))
    monkeypatch.setattr("app.services.step_cancel.is_generation_active", lambda _id: False)
    monkeypatch.setattr(aa, "ready_status_confirmed_by_data", AsyncMock(return_value=True))
    monkeypatch.setattr(aa, "clamp_status_to_data", AsyncMock(return_value=None))
    monkeypatch.setattr(aa, "_harness_gate", AsyncMock(return_value=True))
    monkeypatch.setattr(aa, "_graph_next_running", AsyncMock(return_value=ProjectStatus.publishing))
    monkeypatch.setattr(aa, "get_latest_hitl", get_hitl)
    monkeypatch.setattr(aa, "_apply_approve", apply_mock)
    monkeypatch.setattr(aa, "_record_unchecked_auto_approve", AsyncMock(return_value=None))
    monkeypatch.setattr("app.orchestrator.steps.publish._latest_final",
                        AsyncMock(return_value=SimpleNamespace(uuid=final_uuid)))
    advanced = asyncio.run(aa.maybe_auto_advance(_Sess(), project, bot=None, force=True))
    return advanced, get_hitl, apply_mock


@pytest.mark.parametrize("key", ["publish_awaiting_approval", "publish_needs_human", "publish_closed_by_human"])
@pytest.mark.parametrize("hitl_auto", [False, True])
def test_marker_for_the_same_file_holds_the_project_in_assembled(monkeypatch, key, hitl_auto):
    advanced, get_hitl, apply_mock = _drive(monkeypatch, {key: {"artifact_uuid": "u1", "platforms": ["tiktok"]}},
                                            hitl_auto=hitl_auto)
    assert advanced is False
    get_hitl.assert_not_awaited()  # до HITL не дошли
    apply_mock.assert_not_awaited()  # перехода в publishing нет, даже при auto_mode=True


def test_publish_skipped_holds_only_while_the_flag_is_off(monkeypatch):
    meta = {"publish_skipped": {"artifact_uuid": "u1"}}
    monkeypatch.setattr(settings, "social_publish_enabled", False)
    advanced, get_hitl, apply_mock = _drive(monkeypatch, meta)
    assert advanced is False and apply_mock.await_count == 0 and get_hitl.await_count == 0
    monkeypatch.setattr(settings, "social_publish_enabled", True)
    _drive(monkeypatch, meta)  # флаг включён: удержания нет, обычный путь approve_final -> publishing


def test_marker_for_an_older_file_does_not_hold_a_rebuilt_video(monkeypatch):
    _, get_hitl, _ = _drive(monkeypatch, {"publish_needs_human": {"artifact_uuid": "old", "platforms": ["tiktok"]}},
                            final_uuid="new")
    get_hitl.assert_awaited()  # пересобранный файл идёт обычным путём


def test_without_markers_the_normal_path_is_untouched(monkeypatch):
    _, get_hitl, _ = _drive(monkeypatch, {})
    get_hitl.assert_awaited()
