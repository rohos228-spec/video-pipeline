"""(Mass-gen Фаза 1) Тесты на «кнопку подтверждения после каждого шага».

Проверяем что:
1. В `auto_mode=True` GPT/auto-апруф НЕ продвигает проект —
   статус остаётся в `*_ready` и в meta появляется флаг
   `awaiting_user_confirmation`.
2. После того как юзер реально нажал кнопку
   (`hitl.decision == approved`), `_apply_approve(_user_clicked=True)`
   нормально продвигает проект.
3. В `auto_mode=False` (одиночная генерация) гейт НЕ срабатывает —
   ничего не меняется (важно: не сломать индивидуальный пайплайн).
4. Если `MASS_GEN_REQUIRE_CONFIRMATION=0` (legacy) — auto-approve
   снова продвигает проект.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.models import HITLDecision, HITLKind, HITLRequest, Project, ProjectStatus
from app.orchestrator import auto_advance
from app.orchestrator.auto_advance import TRANSITIONS, _apply_approve


class _StubAsyncSession:
    async def flush(self) -> None:
        return None

    async def execute(self, *_a, **_kw):  # pragma: no cover
        raise AssertionError("execute() should not be called in these tests")


def _make_project(*, status: ProjectStatus, auto_mode: bool) -> Project:
    p = Project(slug="t", topic="t", hero_mode="full_auto")
    p.status = status
    p.enrich_slots_count = 3
    p.hero_count = 1
    p.hero_variations = [1]
    p.auto_mode = auto_mode
    p.meta = {}
    return p


def _make_hitl(kind: HITLKind) -> HITLRequest:
    h = HITLRequest(project_id=1, kind=kind, decision=HITLDecision.pending)
    h.payload = {}
    h.tg_message_id = None
    return h


@pytest.mark.asyncio
async def test_auto_mode_gpt_approve_does_not_advance_status() -> None:
    """Главный кейс: auto_mode=True + GPT-approve (НЕ user-click) →
    статус НЕ меняется + в meta появляется awaiting_user_confirmation."""
    project = _make_project(
        status=ProjectStatus.plan_ready, auto_mode=True
    )
    transition = TRANSITIONS[ProjectStatus.plan_ready]
    hitl = _make_hitl(HITLKind.approve_plan)

    with patch.object(auto_advance, "MASS_GEN_REQUIRE_CONFIRMATION", True):
        await _apply_approve(
            _StubAsyncSession(), project, hitl, transition,
            bot=None, badge="", _user_clicked=False,
        )

    # Статус НЕ должен поменяться.
    assert project.status is ProjectStatus.plan_ready
    # Флаг должен быть выставлен.
    assert (
        (project.meta or {}).get("awaiting_user_confirmation")
        == "plan_ready"
    )
    # HITL decision НЕ менялся (юзер ничего не кликал).
    assert hitl.decision is HITLDecision.pending


@pytest.mark.asyncio
async def test_user_click_advances_status_normally() -> None:
    """Юзер нажал ✅ → _user_clicked=True → проект продвигается."""
    project = _make_project(
        status=ProjectStatus.plan_ready, auto_mode=True
    )
    transition = TRANSITIONS[ProjectStatus.plan_ready]
    # Эмулируем что HITL уже approved юзером в TG.
    hitl = _make_hitl(HITLKind.approve_plan)
    hitl.decision = HITLDecision.approved

    with patch.object(auto_advance, "MASS_GEN_REQUIRE_CONFIRMATION", True):
        await _apply_approve(
            _StubAsyncSession(), project, hitl, transition,
            bot=None, badge="", _user_clicked=True,
        )

    assert project.status is ProjectStatus.scripting
    # Флаг подтверждения сниматься (или не было его — оба ок).
    assert "awaiting_user_confirmation" not in (project.meta or {})


@pytest.mark.asyncio
async def test_user_click_clears_awaiting_flag() -> None:
    """Был флаг awaiting_user_confirmation → юзер нажал → флаг сброшен."""
    project = _make_project(
        status=ProjectStatus.plan_ready, auto_mode=True
    )
    project.meta = {"awaiting_user_confirmation": "plan_ready"}
    transition = TRANSITIONS[ProjectStatus.plan_ready]
    hitl = _make_hitl(HITLKind.approve_plan)
    hitl.decision = HITLDecision.approved

    with patch.object(auto_advance, "MASS_GEN_REQUIRE_CONFIRMATION", True):
        await _apply_approve(
            _StubAsyncSession(), project, hitl, transition,
            bot=None, badge="", _user_clicked=True,
        )

    assert project.status is ProjectStatus.scripting
    assert "awaiting_user_confirmation" not in (project.meta or {})


@pytest.mark.asyncio
async def test_individual_pipeline_not_affected() -> None:
    """auto_mode=False (индивидуальная генерация) → гейт НЕ срабатывает.

    В индивидуальном пайплайне юзер сам кликает кнопки в TG, а
    _apply_approve вообще не должен вызываться без _user_clicked.
    Но если по какой-то причине вызовется — он должен продвигать
    проект как раньше, чтобы не сломать существующий поток.
    """
    project = _make_project(
        status=ProjectStatus.plan_ready, auto_mode=False
    )
    transition = TRANSITIONS[ProjectStatus.plan_ready]
    hitl = _make_hitl(HITLKind.approve_plan)

    with patch.object(auto_advance, "MASS_GEN_REQUIRE_CONFIRMATION", True):
        await _apply_approve(
            _StubAsyncSession(), project, hitl, transition,
            bot=None, badge="", _user_clicked=False,
        )

    # auto_mode=False → гейт не сработал → проект продвинулся.
    assert project.status is ProjectStatus.scripting


@pytest.mark.asyncio
async def test_legacy_mode_advances_without_user_click() -> None:
    """MASS_GEN_REQUIRE_CONFIRMATION=0 (legacy) → auto-approve работает."""
    project = _make_project(
        status=ProjectStatus.plan_ready, auto_mode=True
    )
    transition = TRANSITIONS[ProjectStatus.plan_ready]
    hitl = _make_hitl(HITLKind.approve_plan)

    with patch.object(auto_advance, "MASS_GEN_REQUIRE_CONFIRMATION", False):
        await _apply_approve(
            _StubAsyncSession(), project, hitl, transition,
            bot=None, badge="", _user_clicked=False,
        )

    # Legacy-режим: GPT auto-approve продвигает проект.
    assert project.status is ProjectStatus.scripting


@pytest.mark.asyncio
async def test_gate_blocks_visual_kinds_too() -> None:
    """Гейт работает не только на text-kinds (approve_plan), но и на
    visual-kinds (approve_images, approve_videos и т.д.).

    Это нужно потому что без gate'а maybe_auto_advance делает
    auto-approve для visual-kinds, если не включён AUTO_REVIEW_VISUAL.
    Юзер требовал ПОДТВЕРЖДЕНИЕ ДЛЯ ВСЕХ ШАГОВ 1-11.
    """
    project = _make_project(
        status=ProjectStatus.images_ready, auto_mode=True
    )
    transition = TRANSITIONS[ProjectStatus.images_ready]
    hitl = _make_hitl(HITLKind.approve_images)

    with patch.object(auto_advance, "MASS_GEN_REQUIRE_CONFIRMATION", True):
        await _apply_approve(
            _StubAsyncSession(), project, hitl, transition,
            bot=None, badge="", _user_clicked=False,
        )

    assert project.status is ProjectStatus.images_ready
    assert (
        (project.meta or {}).get("awaiting_user_confirmation")
        == "images_ready"
    )


@pytest.mark.asyncio
async def test_frames_ready_uses_approve_blocks_kind() -> None:
    """(Mass-gen Фаза 2) После шага 3 (разбивка на блоки) transition
    использует HITLKind.approve_blocks, а не приклеенный approve_hero."""
    transition = TRANSITIONS[ProjectStatus.frames_ready]
    assert transition.kind is HITLKind.approve_blocks
    assert transition.next_running is ProjectStatus.generating_hero


@pytest.mark.asyncio
async def test_gate_blocks_blocks_step() -> None:
    """(Mass-gen Фаза 2) Гейт срабатывает на frames_ready (шаг 3 — блоки)."""
    project = _make_project(
        status=ProjectStatus.frames_ready, auto_mode=True
    )
    transition = TRANSITIONS[ProjectStatus.frames_ready]
    hitl = _make_hitl(HITLKind.approve_blocks)

    with patch.object(auto_advance, "MASS_GEN_REQUIRE_CONFIRMATION", True):
        await _apply_approve(
            _StubAsyncSession(), project, hitl, transition,
            bot=None, badge="", _user_clicked=False,
        )

    assert project.status is ProjectStatus.frames_ready
    assert (
        (project.meta or {}).get("awaiting_user_confirmation")
        == "frames_ready"
    )


@pytest.mark.asyncio
async def test_blocks_user_click_advances_to_hero() -> None:
    """После клика на approve_blocks проект уходит в generating_hero."""
    project = _make_project(
        status=ProjectStatus.frames_ready, auto_mode=True
    )
    transition = TRANSITIONS[ProjectStatus.frames_ready]
    hitl = _make_hitl(HITLKind.approve_blocks)
    hitl.decision = HITLDecision.approved

    with patch.object(auto_advance, "MASS_GEN_REQUIRE_CONFIRMATION", True):
        await _apply_approve(
            _StubAsyncSession(), project, hitl, transition,
            bot=None, badge="", _user_clicked=True,
        )

    assert project.status is ProjectStatus.generating_hero


@pytest.mark.asyncio
async def test_gate_blocks_hero_step() -> None:
    """Гейт на hero_ready (шаг 4 — объекты)."""
    project = _make_project(
        status=ProjectStatus.hero_ready, auto_mode=True
    )
    project.hero_count = 1
    project.hero_variations = [1]
    transition = TRANSITIONS[ProjectStatus.hero_ready]
    hitl = _make_hitl(HITLKind.approve_hero)

    with patch.object(auto_advance, "MASS_GEN_REQUIRE_CONFIRMATION", True):
        await _apply_approve(
            _StubAsyncSession(), project, hitl, transition,
            bot=None, badge="", _user_clicked=False,
        )

    # На hero_ready без _user_clicked мы НЕ продвигаемся.
    assert project.status is ProjectStatus.hero_ready
