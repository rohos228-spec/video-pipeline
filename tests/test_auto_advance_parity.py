"""BLOCK D — smoke-тесты `auto_advance` для проверки parity #1, #3, #8.

Архитектура переехала (2026-07/08): `_apply_approve` больше не чистый
переход по таблице TRANSITIONS — навигация идёт по canvas-графу
(`prepare_enrich_chain_for_auto_advance` / graph BFS) и требует БД.
Поэтому parity-решения проверяем на уровне функций, которые их кодируют:

* #1 hero_count / hero_variations → `_next_status_after_hero_approve`
  (non-excel путь чистый: session.execute не вызывается).
* #3 enrich cap → `prepare_enrich_chain_for_auto_advance` (длина цепочки
  задаётся канвасом — сколько excel_gpt-нод реально стоит) + in-memory
  `WorkflowGraph.next_running_after_ready` для «куда дальше».
* #8 `expected_status_progression(...)` — single-source-of-truth для
  последовательности running-статусов (учитывает enrich_slots_count).

Тесты НЕ запускают БД и НЕ дёргают Telegram: `Project` создаётся
in-memory, сессия — заглушка с no-op `flush()`.
"""

from __future__ import annotations

import pytest

from app.models import HITLDecision, HITLKind, HITLRequest, Project, ProjectStatus
from app.orchestrator.auto_advance import (
    TRANSITIONS,
    _next_status_after_hero_approve,
    expected_status_progression,
)
from app.services.excel_gpt_node import prepare_enrich_chain_for_auto_advance


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


def _excel_chain_canvas(n_slots: int) -> dict:
    """Канвас цепочки enrich: script → excel_gpt#1..#N → image_prompts.

    Именно канвас задаёт «сколько слотов enrich реально крутить» —
    enrich_slots_count отражён в нём при сборке графа в Studio.
    """
    nodes = [{"id": "n_script", "type": "script", "data": {}}]
    edges = []
    prev = "n_script"
    for i in range(1, n_slots + 1):
        nid = f"n_gpt{i}"
        nodes.append(
            {
                "id": nid,
                "type": "excel_gpt",
                "position": {"x": i * 100, "y": 0},
                "data": {"slotIndex": i},
            }
        )
        edges.append({"id": f"e_{prev}_{nid}", "source": prev, "target": nid})
        prev = nid
    nodes.append({"id": "n_img_pr", "type": "image_prompts", "data": {}})
    edges.append({"id": f"e_{prev}_imgpr", "source": prev, "target": "n_img_pr"})
    return {"nodes": nodes, "edges": edges}


def test_enrich_cap_2_skips_to_image_prompts() -> None:
    """parity #3: канвас с 2 excel_gpt — после enrich_2_ready цепочка НЕ
    уходит в enriching_3: следующая work-нода по стрелкам — image_prompts."""
    project = _make_project(
        status=ProjectStatus.enrich_2_ready,
        enrich_slots_count=2,
    )
    project.meta = {
        "canvas_graph": _excel_chain_canvas(2),
        "split_completed": True,
        "enrich_completed_slots": [1, 2],
        "excel_gpt_completed_keys": ["n_gpt1", "n_gpt2"],
        "active_excel_gpt_node_key": "n_gpt2",
    }
    # default-таблица TRANSITIONS говорит enriching_3 — она для линейного
    # fallback без канваса; реальное «куда дальше» решает граф.
    transition = TRANSITIONS[ProjectStatus.enrich_2_ready]
    assert transition.next_running is ProjectStatus.enriching_3

    # Следующей excel_gpt по стрелкам нет → цепочка не продолжается.
    nxt = prepare_enrich_chain_for_auto_advance(project, ProjectStatus.enrich_2_ready)
    assert nxt is None

    # …а graph BFS ведёт сразу на «Промты картинок».
    from app.orchestrator.graph.planner import WorkflowGraph

    cg = project.meta["canvas_graph"]
    graph = WorkflowGraph(cg["nodes"], cg["edges"])
    assert (
        graph.next_running_after_ready(project, ProjectStatus.enrich_2_ready)
        is ProjectStatus.generating_image_prompts
    )


def test_enrich_cap_5_uses_full_chain() -> None:
    """parity #3 negative-case: на канвасе есть excel_gpt #3 — после
    enrich_2_ready идём в enriching_3 (скипа нет)."""
    project = _make_project(
        status=ProjectStatus.enrich_2_ready,
        enrich_slots_count=5,
    )
    project.meta = {
        "canvas_graph": _excel_chain_canvas(3),
        "split_completed": True,
        "enrich_completed_slots": [1, 2],
        "excel_gpt_completed_keys": ["n_gpt1", "n_gpt2"],
        "active_excel_gpt_node_key": "n_gpt2",
    }

    nxt = prepare_enrich_chain_for_auto_advance(project, ProjectStatus.enrich_2_ready)
    assert nxt is ProjectStatus.enriching_3
    # Активная нода переключена на следующую по стрелке.
    assert project.meta.get("active_excel_gpt_node_key") == "n_gpt3"


@pytest.mark.asyncio
async def test_hero_count_2_stays_in_generating_hero() -> None:
    """parity #1: с hero_count=2 после approve первого героя следующий
    running — снова generating_hero (нужен 2-й герой)."""
    project = _make_project(
        status=ProjectStatus.hero_ready,
        hero_count=2,
        hero_variations=[1, 1],
    )
    transition = TRANSITIONS[ProjectStatus.hero_ready]
    # default-таблица → generating_items; parity #1 переписывает на
    # generating_hero внутри `_next_status_after_hero_approve`.
    assert transition.next_running is ProjectStatus.generating_items
    hitl = _make_hitl(
        HITLKind.approve_hero,
        payload={"hero_index": 1, "variation_index": 1},
    )
    nxt = await _next_status_after_hero_approve(
        _StubAsyncSession(), project, hitl
    )
    assert nxt is ProjectStatus.generating_hero


@pytest.mark.asyncio
async def test_hero_count_1_advances_to_items() -> None:
    """parity #1 negative-case: с hero_count=1 после approve последнего
    героя следующий running — generating_items."""
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


@pytest.mark.asyncio
async def test_hero_variations_3_stays_for_remaining_variations() -> None:
    """parity #1: с hero_variations=[3] после approve вариации 1 из 3
    следующий running — снова generating_hero (остались вариации)."""
    project = _make_project(
        status=ProjectStatus.hero_ready,
        hero_count=1,
        hero_variations=[3],
    )
    hitl = _make_hitl(
        HITLKind.approve_hero,
        payload={"hero_index": 1, "variation_index": 1},
    )
    nxt = await _next_status_after_hero_approve(
        _StubAsyncSession(), project, hitl
    )
    assert nxt is ProjectStatus.generating_hero
    # А после последней вариации — дальше.
    hitl_last = _make_hitl(
        HITLKind.approve_hero,
        payload={"hero_index": 1, "variation_index": 3},
    )
    nxt = await _next_status_after_hero_approve(
        _StubAsyncSession(), project, hitl_last
    )
    assert nxt is ProjectStatus.generating_items


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
    """auto_mode: pending HITL не блокирует переход — visual auto-approve.

    maybe_auto_advance всегда идёт через ИИ-контроль (отдельного флага
    ai_control больше нет); для visual-kind без включённого vision-чека
    решение — сразу `_apply_approve`. DB-гварды (clamp/confirm/очередь)
    здесь замоканы — они покрыты интеграционными тестами gen_queue.
    """
    from unittest.mock import AsyncMock

    from app.orchestrator import auto_advance as aa
    from app.orchestrator.auto_advance import maybe_auto_advance

    project = _make_project(status=ProjectStatus.frames_ready, auto_mode=True)
    hitl = _make_hitl(HITLKind.approve_hero)
    apply_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(aa, "get_latest_hitl", AsyncMock(return_value=hitl))
    monkeypatch.setattr(aa, "_apply_approve", apply_mock)
    monkeypatch.setattr(aa, "clamp_status_to_data", AsyncMock(return_value=None))
    monkeypatch.setattr(
        aa, "ready_status_confirmed_by_data", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        "app.services.gen_queue.project_gated_by_gen_queue", lambda _id: False
    )
    monkeypatch.setattr(
        "app.services.gen_queue.gen_queue_blocks_project",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        "app.services.step_cancel.is_generation_active", lambda _id: False
    )

    advanced = await maybe_auto_advance(_StubAsyncSession(), project, bot=None)

    assert advanced is True
    apply_mock.assert_awaited_once()
