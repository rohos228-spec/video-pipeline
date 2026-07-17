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
    _next_status_after_hero_approve,
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


@pytest.fixture
def patch_apply_approve_graph(monkeypatch: pytest.MonkeyPatch):
    """_apply_approve тянет canvas graph из БД — в smoke-тестах подменяем."""
    from unittest.mock import AsyncMock

    from app.orchestrator.auto_advance import _next_running_with_enrich_cap

    async def _identity_status(_session, project, status, **_kwargs):
        return status

    async def _graph_next(_session, project, ready):
        return _next_running_with_enrich_cap(project, TRANSITIONS[ready])

    monkeypatch.setattr(
        "app.orchestrator.auto_advance._graph_next_running",
        AsyncMock(side_effect=_graph_next),
    )
    monkeypatch.setattr(
        "app.orchestrator.auto_advance.skip_disabled_running_async",
        AsyncMock(side_effect=_identity_status),
    )
    monkeypatch.setattr(
        "app.orchestrator.auto_advance._apply_running_if_data_ok",
        AsyncMock(side_effect=_identity_status),
    )
    monkeypatch.setattr(
        "app.orchestrator.auto_advance._prepare_node_run_for_status",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.excel_gpt_node.prepare_enrich_chain_for_auto_advance",
        lambda project, ready: None,
    )


@pytest.mark.asyncio
async def test_enrich_cap_2_skips_to_image_prompts(patch_apply_approve_graph) -> None:
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
async def test_enrich_cap_5_uses_full_chain(patch_apply_approve_graph) -> None:
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
async def test_hero_count_2_stays_in_generating_hero(patch_apply_approve_graph) -> None:
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
async def test_hero_count_1_advances_to_items(patch_apply_approve_graph) -> None:
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
async def test_hero_variations_3_stays_for_remaining_variations(
    patch_apply_approve_graph,
) -> None:
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


@pytest.mark.asyncio
async def test_auto_mode_advances_without_manual_approval(monkeypatch) -> None:
    """auto_mode без ai_control: pending HITL не блокирует переход."""
    from unittest.mock import AsyncMock

    from app.orchestrator.auto_advance import maybe_auto_advance

    project = _make_project(status=ProjectStatus.frames_ready, auto_mode=True)
    project.meta = {"ai_control": False}
    hitl = _make_hitl(HITLKind.approve_hero)
    apply_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "app.orchestrator.auto_advance.get_latest_hitl",
        AsyncMock(return_value=hitl),
    )
    monkeypatch.setattr("app.orchestrator.auto_advance._apply_approve", apply_mock)
    monkeypatch.setattr(
        "app.orchestrator.auto_advance.clamp_status_to_data",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.orchestrator.auto_advance.ready_status_confirmed_by_data",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "app.services.step_cancel.is_generation_active",
        lambda _pid: False,
    )
    monkeypatch.setattr(
        "app.services.gen_queue.project_gated_by_gen_queue",
        lambda _pid: False,
    )
    monkeypatch.setattr(
        "app.services.gen_queue.gen_queue_blocks_project",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.gen_queue_run.should_hold_queue_auto_advance",
        lambda _p: False,
    )
    monkeypatch.setattr(
        "app.services.project_control.auto_awaits_manual_start",
        lambda _p: False,
    )
    monkeypatch.setattr(
        "app.services.gen_queue_run.is_user_stopped",
        lambda _p: False,
    )

    advanced = await maybe_auto_advance(_StubAsyncSession(), project, bot=None)

    assert advanced is True
    apply_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_excel_batch_all_on_disk_leaves_hero_step(monkeypatch) -> None:
    """batch auto_mode: все c0N.png на диске → items, не цикл generating_hero.

    Раньше stale HITL с excel_id=c01 + hero_count=5 возвращал generating_hero,
    воркер снова ставил hero_ready («все артефакты на диске») — вечный цикл.
    """
    from unittest.mock import AsyncMock

    project = _make_project(
        status=ProjectStatus.hero_ready,
        hero_count=5,
        hero_variations=[1, 1, 1, 1, 1],
    )
    project.id = 42
    project.meta = {
        "excel_hero": {
            "characters": [
                {"id": "c01"},
                {"id": "c02"},
                {"id": "c03"},
                {"id": "c04"},
                {"id": "c05"},
            ]
        }
    }
    monkeypatch.setattr(
        "app.orchestrator.steps.generate_hero._excel_ids_with_artifact",
        AsyncMock(return_value={"c01", "c02", "c03", "c04", "c05"}),
    )
    monkeypatch.setattr(
        "app.orchestrator.steps.generate_hero._is_regen_for_excel_id",
        AsyncMock(return_value=False),
    )
    hitl = _make_hitl(
        HITLKind.approve_hero,
        payload={"excel_id": "c01"},
    )
    nxt = await _next_status_after_hero_approve(
        _StubAsyncSession(), project, hitl
    )
    assert nxt is ProjectStatus.generating_items


@pytest.mark.asyncio
async def test_excel_missing_files_stays_in_generating_hero(monkeypatch) -> None:
    from unittest.mock import AsyncMock

    project = _make_project(status=ProjectStatus.hero_ready, hero_count=2)
    project.id = 7
    project.meta = {
        "excel_hero": {"characters": [{"id": "c01"}, {"id": "c02"}]}
    }
    monkeypatch.setattr(
        "app.orchestrator.steps.generate_hero._excel_ids_with_artifact",
        AsyncMock(return_value={"c01"}),
    )
    monkeypatch.setattr(
        "app.orchestrator.steps.generate_hero._is_regen_for_excel_id",
        AsyncMock(return_value=False),
    )
    hitl = _make_hitl(HITLKind.approve_hero, payload={"excel_id": "c01"})
    nxt = await _next_status_after_hero_approve(
        _StubAsyncSession(), project, hitl
    )
    assert nxt is ProjectStatus.generating_hero


@pytest.mark.asyncio
async def test_hero_step_level_approve_without_hero_index_advances() -> None:
    """hitl=None / пустой payload не должен подставлять hero_index=1 при hero_count>1."""
    project = _make_project(
        status=ProjectStatus.hero_ready,
        hero_count=5,
        hero_variations=[1, 1, 1, 1, 1],
    )
    nxt = await _next_status_after_hero_approve(
        _StubAsyncSession(), project, None
    )
    assert nxt is ProjectStatus.generating_items


@pytest.mark.asyncio
async def test_next_status_hero_count_2_stays_generating() -> None:
    project = _make_project(
        status=ProjectStatus.hero_ready,
        hero_count=2,
        hero_variations=[1, 1],
    )
    hitl = _make_hitl(
        HITLKind.approve_hero,
        payload={"hero_index": 1, "variation_index": 1},
    )
    nxt = await _next_status_after_hero_approve(
        _StubAsyncSession(), project, hitl
    )
    assert nxt is ProjectStatus.generating_hero


@pytest.mark.asyncio
async def test_next_status_hero_count_1_advances_items() -> None:
    project = _make_project(
        status=ProjectStatus.hero_ready,
        hero_count=1,
        hero_variations=[1],
    )
    hitl = _make_hitl(
        HITLKind.approve_hero,
        payload={"hero_index": 1, "variation_index": 1},
    )
    nxt = await _next_status_after_hero_approve(
        _StubAsyncSession(), project, hitl
    )
    assert nxt is ProjectStatus.generating_items
