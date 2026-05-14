"""BLOCK D — smoke-тесты `auto_advance` для проверки parity #1, #3.

Эти тесты НЕ запускают БД и НЕ дёргают Telegram. Мы создаём `Project`
inmemory (без сохранения) и вызываем `_apply_approve(...)`, передавая
заглушку async-session, у которой `flush()` — no-op, а `execute()` — не
вызывается (для не-excel hero и для enrich-cap пути это безопасно).

Покрываются 3 ключевых parity-расхождения:
* #1 hero_count = 2 → после approve 1-го героя проект ДОЛЖЕН остаться
  в `generating_hero`, а не уйти в `generating_items`.
* #3 enrich_slots_count = 2 → после `enrich_2_ready` approve проект
  ДОЛЖЕН уйти в `generating_image_prompts`, а не в `enriching_3`.
* parity #8 `expected_status_progression(...)` корректно отражает оба
  кейса (single-source-of-truth для будущих тестов).
"""

from __future__ import annotations

import pytest

from app.models import HITLDecision, HITLKind, HITLRequest, Project, ProjectStatus
from app.orchestrator.auto_advance import (
    TRANSITIONS,
    _apply_approve,
    expected_status_progression,
)


class _StubAsyncSession:
    """Минимальный stub: `flush()` — no-op. `execute()` зовётся только
    в excel-hero ветке, которую тут не проверяем."""

    async def flush(self) -> None:
        return None

    async def execute(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError(
            "execute() should not be called in these test cases"
        )


def _make_project(
    *,
    status: ProjectStatus,
    enrich_slots_count: int = 3,
    hero_count: int = 1,
    hero_variations: list | None = None,
    auto_mode: bool = True,
) -> Project:
    """Транзиентный Project — мы НЕ добавляем его в session, поэтому
    SQLAlchemy не валидирует обязательные поля."""
    p = Project(
        slug="t",
        topic="t",
        hero_mode="full_auto",
    )
    p.status = status
    p.enrich_slots_count = enrich_slots_count
    p.hero_count = hero_count
    p.hero_variations = list(hero_variations or [1])
    p.auto_mode = auto_mode
    p.meta = {}
    return p


def _make_hitl(kind: HITLKind, payload: dict | None = None) -> HITLRequest:
    h = HITLRequest(
        project_id=1,
        kind=kind,
        decision=HITLDecision.pending,
    )
    h.payload = dict(payload or {})
    h.tg_message_id = None
    return h


@pytest.mark.asyncio
async def test_enrich_cap_2_skips_to_image_prompts() -> None:
    """parity #3: с enrich_slots_count=2 после enrich_2_ready
    проект уходит в generating_image_prompts, а НЕ в enriching_3."""
    project = _make_project(
        status=ProjectStatus.enrich_2_ready,
        enrich_slots_count=2,
    )
    transition = TRANSITIONS[ProjectStatus.enrich_2_ready]
    # default-таблица говорит enriching_3, но _next_running_with_enrich_cap
    # ДОЛЖЕН переписать на generating_image_prompts.
    assert transition.next_running is ProjectStatus.enriching_3
    hitl = _make_hitl(HITLKind.approve_hero)
    await _apply_approve(
        _StubAsyncSession(), project, hitl, transition,
        bot=None, badge="",
    )
    assert project.status is ProjectStatus.generating_image_prompts


@pytest.mark.asyncio
async def test_enrich_cap_5_uses_full_chain() -> None:
    """parity #3 negative-case: с enrich_slots_count=5 после
    enrich_2_ready НЕ скипаем, идём в enriching_3."""
    project = _make_project(
        status=ProjectStatus.enrich_2_ready,
        enrich_slots_count=5,
    )
    transition = TRANSITIONS[ProjectStatus.enrich_2_ready]
    hitl = _make_hitl(HITLKind.approve_hero)
    await _apply_approve(
        _StubAsyncSession(), project, hitl, transition,
        bot=None, badge="",
    )
    assert project.status is ProjectStatus.enriching_3


@pytest.mark.asyncio
async def test_hero_count_2_stays_in_generating_hero() -> None:
    """parity #1: с hero_count=2 после approve первого героя
    проект ДОЛЖЕН остаться в generating_hero (нужен 2-й герой)."""
    project = _make_project(
        status=ProjectStatus.hero_ready,
        hero_count=2,
        hero_variations=[1, 1],
    )
    transition = TRANSITIONS[ProjectStatus.hero_ready]
    # default → generating_items, parity #1 ДОЛЖЕН переписать на
    # generating_hero.
    assert transition.next_running is ProjectStatus.generating_items
    hitl = _make_hitl(
        HITLKind.approve_hero,
        payload={"hero_index": 1, "variation_index": 1},
    )
    await _apply_approve(
        _StubAsyncSession(), project, hitl, transition,
        bot=None, badge="",
    )
    assert project.status is ProjectStatus.generating_hero


@pytest.mark.asyncio
async def test_hero_count_1_advances_to_items() -> None:
    """parity #1 negative-case: с hero_count=1 после approve последнего
    героя проект уходит в generating_items."""
    project = _make_project(
        status=ProjectStatus.hero_ready,
        hero_count=1,
        hero_variations=[1],
    )
    transition = TRANSITIONS[ProjectStatus.hero_ready]
    hitl = _make_hitl(
        HITLKind.approve_hero,
        payload={"hero_index": 1, "variation_index": 1},
    )
    await _apply_approve(
        _StubAsyncSession(), project, hitl, transition,
        bot=None, badge="",
    )
    assert project.status is ProjectStatus.generating_items


@pytest.mark.asyncio
async def test_hero_variations_3_stays_for_remaining_variations() -> None:
    """parity #1: с hero_variations=[3] после approve вариации 1 из 3
    проект ДОЛЖЕН остаться в generating_hero."""
    project = _make_project(
        status=ProjectStatus.hero_ready,
        hero_count=1,
        hero_variations=[3],
    )
    transition = TRANSITIONS[ProjectStatus.hero_ready]
    hitl = _make_hitl(
        HITLKind.approve_hero,
        payload={"hero_index": 1, "variation_index": 1},
    )
    await _apply_approve(
        _StubAsyncSession(), project, hitl, transition,
        bot=None, badge="",
    )
    assert project.status is ProjectStatus.generating_hero


def test_expected_progression_respects_enrich_slots() -> None:
    """parity #8: expected_status_progression возвращает ровно
    N enrich-шагов и не больше."""
    p = _make_project(
        status=ProjectStatus.planning, enrich_slots_count=2
    )
    prog = expected_status_progression(p)
    enriching = [s for s in prog if s.value.startswith("enriching_")]
    assert enriching == [ProjectStatus.enriching_1, ProjectStatus.enriching_2]
    # Шаг сразу после enriching_2 — generating_image_prompts.
    idx = prog.index(ProjectStatus.enriching_2)
    assert prog[idx + 1] is ProjectStatus.generating_image_prompts


def test_expected_progression_default_3_slots() -> None:
    """parity #8: дефолт = 3 слота."""
    p = _make_project(
        status=ProjectStatus.planning, enrich_slots_count=3
    )
    prog = expected_status_progression(p)
    enriching = [s for s in prog if s.value.startswith("enriching_")]
    assert enriching == [
        ProjectStatus.enriching_1,
        ProjectStatus.enriching_2,
        ProjectStatus.enriching_3,
    ]
