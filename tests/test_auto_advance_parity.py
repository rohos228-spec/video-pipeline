"""BLOCK D — smoke-тесты `auto_advance` для проверки parity #1, #3.

Эти тесты НЕ запускают БД и НЕ дёргают Telegram. Мы создаём `Project`
inmemory (без сохранения) и вызываем `_apply_approve(...)`, передавая
заглушку async-session, у которой `flush()` — no-op, а `execute()` — не
вызывается (для не-excel hero и для enrich-cap пути это безопасно).

Покрываются 3 ключевых parity-расхождения:
* #1 hero_count = 2 → после approve 1-го героя проект ДОЛЖЕН остаться
  в `generating_hero`, а не уйти в `generating_items`.
* #3 (переписан под граф, см. 6e3202c9) цепочка enrich на канвасе
  (enrich_1..2 -> image_prompts) → после `enrich_2_ready` проект уходит в
  `generating_image_prompts`, а не в `enriching_3`.
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


class _EmptyResult:
    """Результат SELECT без строк (все способы чтения)."""

    def scalar_one_or_none(self):
        return None

    def scalar_one(self):
        return 0

    def scalar(self):
        return None

    def first(self):
        return None

    def all(self):
        return []

    def scalars(self):
        return self

    def __iter__(self):
        return iter(())


class _StubAsyncSession:
    """Минимальный stub: `flush()` — no-op. `execute()` зовётся только
    в excel-hero ветке, которую тут не проверяем."""

    async def flush(self) -> None:
        return None

    async def execute(self, *_args, **_kwargs):
        # Продвижение теперь грузит граф проекта (load_graph_for_project ->
        # WorkflowRun) и проверяет данные шага (step_data_guard): в этих кейсах
        # БД «пуста» -> пустой результат, поведение parity-таблицы не меняется.
        return _EmptyResult()


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


def _enrich_chain_graph(last_slot: int):
    """Граф канваса: enrich_1 -> ... -> enrich_<last_slot> -> image_prompts."""
    from app.orchestrator.graph.planner import WorkflowGraph

    types = [f"enrich_{i}" for i in range(1, last_slot + 1)] + ["image_prompts"]
    nodes = [{"id": f"n_{t}", "type": t, "position": {"x": 0, "y": 0}, "data": {}} for t in types]
    edges = [
        {"id": f"e{i}", "source": f"n_{a}", "target": f"n_{b}",
         "sourceHandle": "out", "targetHandle": "in"}
        for i, (a, b) in enumerate(zip(types, types[1:]))
    ]
    return WorkflowGraph(nodes, edges)


def _use_graph(monkeypatch, graph) -> None:
    async def _load(_session, _project):
        return graph

    monkeypatch.setattr("app.orchestrator.graph.planner.load_graph_for_project", _load)

    # Гейт данных (can_enter_running: нужен voiceover и т.п.) здесь не предмет теста:
    # проверяем выбор следующего шага по графу, поэтому пропускаем любой target.
    async def _can_enter(_session, _project, _target):
        return True, "", None

    monkeypatch.setattr("app.orchestrator.auto_advance.can_enter_running", _can_enter)


# Дизайн (6e3202c9 «remove graph_executor toggle, always follow canvas edges»):
# следующий шаг после *_ready определяет ТОЛЬКО граф канваса; прежняя функция
# `_next_running_with_enrich_cap` (parity #3, enrich_slots_count) больше не вызывается
# (мёртвый код), число enrich-слотов задаётся цепочкой нод/disabled_nodes на канвасе.
# Тесты parity #3 переписаны под это поведение.
@pytest.mark.asyncio
async def test_enrich_cap_2_skips_to_image_prompts(monkeypatch) -> None:
    """Граф с цепочкой enrich_1..2 -> image_prompts: после enrich_2_ready проект
    уходит в generating_image_prompts, а НЕ в enriching_3 (cap задан графом;
    enrich_slots_count на выбор шага не влияет)."""
    _use_graph(monkeypatch, _enrich_chain_graph(2))
    project = _make_project(
        status=ProjectStatus.enrich_2_ready,
        enrich_slots_count=5,
    )
    transition = TRANSITIONS[ProjectStatus.enrich_2_ready]
    # default-таблица говорит enriching_3, но граф канваса приоритетнее.
    assert transition.next_running is ProjectStatus.enriching_3
    hitl = _make_hitl(HITLKind.approve_hero)
    await _apply_approve(
        _StubAsyncSession(), project, hitl, transition,
        bot=None, badge="",
    )
    assert project.status is ProjectStatus.generating_image_prompts


@pytest.mark.asyncio
async def test_enrich_cap_5_uses_full_chain(monkeypatch) -> None:
    """Граф с цепочкой enrich_1..5: после enrich_2_ready идём в enriching_3
    (даже при enrich_slots_count=2 - поле не участвует в выборе шага)."""
    _use_graph(monkeypatch, _enrich_chain_graph(5))
    project = _make_project(
        status=ProjectStatus.enrich_2_ready,
        enrich_slots_count=2,
    )
    transition = TRANSITIONS[ProjectStatus.enrich_2_ready]
    hitl = _make_hitl(HITLKind.approve_hero)
    await _apply_approve(
        _StubAsyncSession(), project, hitl, transition,
        bot=None, badge="",
    )
    assert project.status is ProjectStatus.enriching_3


@pytest.mark.asyncio
async def test_enrich_no_next_node_in_graph_does_not_advance(monkeypatch) -> None:
    """Пустой граф (нет ребра после enrich_2): статус не меняется (warning 'check canvas edges')."""
    from app.orchestrator.graph.planner import WorkflowGraph

    _use_graph(monkeypatch, WorkflowGraph([], []))
    project = _make_project(status=ProjectStatus.enrich_2_ready)
    await _apply_approve(
        _StubAsyncSession(), project, _make_hitl(HITLKind.approve_hero),
        TRANSITIONS[ProjectStatus.enrich_2_ready], bot=None, badge="",
    )
    assert project.status is ProjectStatus.enrich_2_ready


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
    # Тест проверяет только «pending HITL не блокирует auto_mode»: гейты данных
    # (clamp_status_to_data сбрасывал frames_ready -> new на пустой БД, harness,
    # gen_queue, ready_status_confirmed_by_data) в unit-тесте без БД отключаем.
    async def _noop(*_a, **_k):
        return None

    async def _true(*_a, **_k):
        return True

    monkeypatch.setattr("app.orchestrator.auto_advance.clamp_status_to_data", _noop)
    monkeypatch.setattr("app.orchestrator.auto_advance._harness_gate", _true)
    monkeypatch.setattr("app.orchestrator.auto_advance.ready_status_confirmed_by_data", _true)
    monkeypatch.setattr("app.services.gen_queue.project_gated_by_gen_queue", lambda _pid: False)
    monkeypatch.setattr("app.services.gen_queue.gen_queue_blocks_project", _noop)

    advanced = await maybe_auto_advance(_StubAsyncSession(), project, bot=None)

    assert advanced is True
    apply_mock.assert_awaited_once()


@pytest.fixture(autouse=True)
def _no_managed_execution(monkeypatch):
    """Observe-v1 managed-execution читает глобальную БД observer (в unit-тесте не поднята)."""

    async def _off(_pid):
        return False

    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", _off)
