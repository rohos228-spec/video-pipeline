"""excel_gpt с slotOverflow (слот 0): ▶ не должен давать 500."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Base,
    NodeRun,
    NodeRunStatus,
    Project,
    ProjectStatus,
    Workflow,
    WorkflowRun,
    WorkflowRunStatus,
)
from app.orchestrator.graph.planner import WorkflowGraph
from app.orchestrator.node_registry import spec_for_node
from app.services.excel_gpt_node import (
    ready_status_for_slot,
    running_status_for_slot,
    slot_index_from_node,
)
from app.services.project_steps import start_step

# Группа «Сценарий → промпты кадров + QC» на канвасе: overflow.
_OVERFLOW_GROUP = (
    ("n_excel_gpt_fw_script", "GPT: сценарист"),
    ("n_excel_gpt_fw_check_script", "Проверка: сценарий"),
    ("n_excel_gpt_fw_action", "GPT: главное действие · по битам"),
    ("n_excel_gpt_fw_shots", "GPT: сцены → кадры"),
    ("n_excel_gpt_fw_frames", "GPT: промты кадров · continuity"),
    ("n_excel_gpt_fw_qc", "GPT: QC промптов"),
)


def _overflow_node(nid: str, label: str, x: float) -> dict:
    return {
        "id": nid,
        "type": "excel_gpt",
        "position": {"x": x, "y": 0},
        "data": {
            "label": label,
            "slotOverflow": True,
            "groupId": "scenariy_prompty_kadrov_qc",
            "groupTitle": "Сценарий → промпты кадров + QC",
        },
    }


def _overflow_nodes() -> list[dict]:
    return [
        _overflow_node(nid, label, i * 290)
        for i, (nid, label) in enumerate(_OVERFLOW_GROUP)
    ]


def _overflow_edges() -> list[dict]:
    pairs = [
        ("n_excel_gpt_fw_script", "n_excel_gpt_fw_check_script"),
        ("n_excel_gpt_fw_check_script", "n_excel_gpt_fw_action"),
        ("n_excel_gpt_fw_action", "n_excel_gpt_fw_shots"),
        ("n_excel_gpt_fw_shots", "n_excel_gpt_fw_frames"),
        ("n_excel_gpt_fw_frames", "n_excel_gpt_fw_qc"),
    ]
    return [
        {"id": f"e{i}", "source": a, "target": b}
        for i, (a, b) in enumerate(pairs, start=1)
    ]


def test_overflow_slot_is_zero() -> None:
    n = _overflow_node("n_x", "GPT: сценарист", 0)
    assert slot_index_from_node(n) == 0


def test_backfill_puts_script_in_completed_when_check_starts(tmp_path) -> None:
    """#63: ⏹ снял сценариста с completed_keys, проверка стартовала без него."""
    from app.services.excel_gpt_node import backfill_overflow_completed_predecessors

    script = "n_excel_gpt_fw_script"
    check = "n_excel_gpt_fw_check_script"
    data_dir = tmp_path / "tkach"
    art = data_dir / "excel_gpt_uploads" / script
    art.mkdir(parents=True)
    (art / "gpt_reply.txt").write_text('{"ops":[1]}', encoding="utf-8")
    project = SimpleNamespace(
        meta={
            "canvas_graph": {
                "nodes": [
                    {"id": script, "type": "excel_gpt"},
                    {"id": check, "type": "excel_gpt"},
                ],
                "edges": [{"source": script, "target": check}],
            },
            "excel_gpt_completed_keys": [check],
        },
        data_dir=data_dir,
    )
    added = backfill_overflow_completed_predecessors(project, check)
    assert added == [script]
    assert script in (project.meta.get("excel_gpt_completed_keys") or [])


def test_running_status_for_overflow_slot_does_not_raise() -> None:
    """Раньше KeyError: 0 → HTTP 500 на ▶ overflow-ноды."""
    assert running_status_for_slot(0) is ProjectStatus.enriching_1
    assert ready_status_for_slot(0) is ProjectStatus.enrich_1_ready
    assert running_status_for_slot(99) is ProjectStatus.enriching_1


def test_spec_for_overflow_excel_gpt() -> None:
    spec = spec_for_node(_overflow_node("n_x", "GPT: сценарист", 0))
    assert spec is not None
    assert spec.step_code == "excel_gpt"
    assert spec.running_status is ProjectStatus.enriching_1
    assert spec.ready_status is ProjectStatus.enrich_1_ready


def test_derived_states_overflow_group_does_not_crash() -> None:
    nodes = _overflow_nodes()
    edges = [
        {
            "id": f"e_{a}_{b}",
            "source": a,
            "target": b,
            "data": {"kind": "after"},
        }
        for (a, _), (b, _) in zip(_OVERFLOW_GROUP, _OVERFLOW_GROUP[1:])
    ]
    graph = WorkflowGraph(nodes, edges)
    project = SimpleNamespace(
        status=ProjectStatus.plan_ready,
        meta={"canvas_graph": {"nodes": nodes, "edges": edges}},
        topic="t",
    )
    states = graph.derived_node_states(project)
    assert set(states) >= {n["id"] for n in nodes}


def test_prepare_overflow_chain_advances_to_next() -> None:
    from app.services.excel_gpt_node import prepare_enrich_chain_for_auto_advance

    nodes = _overflow_nodes()
    edges = _overflow_edges()
    p = SimpleNamespace(
        id=60,
        status=ProjectStatus.enrich_1_ready,
        meta={
            "canvas_graph": {"nodes": nodes, "edges": edges},
            "active_excel_gpt_node_key": "n_excel_gpt_fw_script",
            "excel_gpt_completed_keys": ["n_excel_gpt_fw_script"],
        },
    )
    nxt = prepare_enrich_chain_for_auto_advance(
        p,
        ProjectStatus.enrich_1_ready,
        finished_key="n_excel_gpt_fw_script",
    )
    assert nxt is ProjectStatus.enriching_1
    assert p.meta["active_excel_gpt_node_key"] == "n_excel_gpt_fw_check_script"


def test_overflow_qc_does_not_chain_into_scene_agent() -> None:
    """После QC overflow-группы не хватать world/style как excel_gpt slot 1."""
    from app.models import ProjectStatus
    from app.services.excel_gpt_node import (
        first_work_successor_along_edges,
        prepare_enrich_chain_for_auto_advance,
        resolve_excel_gpt_node_key_for_slot,
    )

    nodes = _overflow_nodes() + [
        {
            "id": "n_excel_gpt_sd_cd_world",
            "type": "excel_gpt",
            "position": {"x": 50, "y": 0},
            "data": {"sd_agent": "world", "label": "мир"},
        },
        {
            "id": "n_excel_gpt_sd_cd_style",
            "type": "excel_gpt",
            "position": {"x": 80, "y": 0},
            "data": {"sd_agent": "style", "label": "стиль"},
        },
        {"id": "n_script", "type": "script", "position": {"x": 2000, "y": 0}, "data": {}},
    ]
    edges = _overflow_edges() + [
        {"id": "e_qc_script", "source": "n_excel_gpt_fw_qc", "target": "n_script"},
        {"id": "e_world_style", "source": "n_excel_gpt_sd_cd_world", "target": "n_excel_gpt_sd_cd_style"},
    ]
    p = SimpleNamespace(
        id=60,
        status=ProjectStatus.enrich_4_ready,
        meta={
            "canvas_graph": {"nodes": nodes, "edges": edges},
            "excel_gpt_completed_keys": [nid for nid, _ in _OVERFLOW_GROUP],
        },
    )
    assert first_work_successor_along_edges(p, "n_excel_gpt_fw_qc") == (
        "n_script",
        "script",
    )
    assert first_work_successor_along_edges(p, "n_excel_gpt_sd_cd_world") == (
        "n_excel_gpt_sd_cd_style",
        "sd_agent",
    )
    assert (
        prepare_enrich_chain_for_auto_advance(p, ProjectStatus.enrich_4_ready)
        is None
    )
    assert resolve_excel_gpt_node_key_for_slot(p, 1) != "n_excel_gpt_sd_cd_world"
    assert resolve_excel_gpt_node_key_for_slot(p, 1) != "n_excel_gpt_sd_cd_style"


def test_overflow_qc_does_not_start_stray_slot1_then_hero() -> None:
    """После QC overflow-группы не хватать чужой excel_gpt slot=1 → hero.

    Канвас #60: группа fw_* (slotOverflow) без исходящего ребра с QC,
    плюс leftover n_excel_gpt_* slot=1 → персонажи → промты картинок.
    enrich_1_ready раньше делал BFS от slot=1 и запускал hero.
    """
    from app.models import Project
    from app.services.excel_gpt_node import prepare_enrich_chain_for_auto_advance

    stray = "n_excel_gpt_1787849035232"
    nodes = _overflow_nodes() + [
        {
            "id": stray,
            "type": "excel_gpt",
            "position": {"x": 0, "y": 200},
            "data": {"slotIndex": 1, "label": "Работа с GPT"},
        },
        {
            "id": "n_hero_1786493259413",
            "type": "hero",
            "position": {"x": 200, "y": 200},
            "data": {"label": "Персонажи"},
        },
        {
            "id": "n_image_prompts",
            "type": "image_prompts",
            "position": {"x": 400, "y": 200},
            "data": {"label": "Промты картинок"},
        },
    ]
    edges = _overflow_edges() + [
        {"id": "e_stray_hero", "source": stray, "target": "n_hero_1786493259413"},
        {
            "id": "e_hero_imgpr",
            "source": "n_hero_1786493259413",
            "target": "n_image_prompts",
        },
    ]
    g = WorkflowGraph(nodes, edges)
    p = Project(
        topic="t",
        slug="t",
        status=ProjectStatus.enrich_1_ready,
        meta={
            "canvas_graph": {"nodes": nodes, "edges": edges},
            "active_excel_gpt_node_key": "n_excel_gpt_fw_qc",
            "excel_gpt_completed_keys": [nid for nid, _ in _OVERFLOW_GROUP],
        },
    )
    assert prepare_enrich_chain_for_auto_advance(p, ProjectStatus.enrich_1_ready) is None
    found = g.next_work_node_after_ready(p, ProjectStatus.enrich_1_ready)
    assert found is None, f"after overflow QC started {found}"
    assert g.next_running_after_ready(p, ProjectStatus.enrich_1_ready) is None


def test_detect_qc_prompt_body_is_not_frame_fill() -> None:
    from pathlib import Path

    from app.orchestrator.steps.enrich_xlsx import (
        _is_frame_prompts_prompt,
        _is_qc_prompts_prompt,
    )

    for candidate in (
        Path("templates/node_groups/script_frames_qc/prompts_qc_continuity_ru.md"),
        Path("templates/excel_gpt_agents/prompts_qc_continuity_ru.md"),
    ):
        if candidate.is_file():
            body = candidate.read_text(encoding="utf-8")
            break
    else:
        body = (
            "Нода: excel_gpt. ПОСЛЕ агента-конвертера\n"
            "(frame_prompts_continuity) и ДО генерации картинок.\n"
            "qc промпт continuity\n"
        )
    assert _is_qc_prompts_prompt("prompts_qc_continuity_ru", body)
    assert not _is_frame_prompts_prompt("prompts_qc_continuity_ru", body)


@pytest.fixture
async def mem_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _scope():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr("app.db.session_scope", _scope)
    monkeypatch.setattr("app.services.run_sync.session_scope", _scope)
    yield _scope
    await engine.dispose()


async def _seed_overflow_group(scope):
    slug = f"ovf-{uuid.uuid4().hex[:8]}"
    nodes = _overflow_nodes()
    edges = _overflow_edges()
    async with scope() as session:
        wf = Workflow(
            name=f"wf-{uuid.uuid4().hex[:8]}",
            is_default=True,
            nodes=nodes,
            edges=edges,
        )
        session.add(wf)
        await session.flush()
        project = Project(
            slug=slug,
            topic="t",
            status=ProjectStatus.plan_ready,
            meta={
                "canvas_graph": {
                    "workflow_id": wf.id,
                    "nodes": nodes,
                    "edges": edges,
                }
            },
        )
        session.add(project)
        await session.flush()
        run = WorkflowRun(
            project_id=project.id,
            workflow_id=wf.id,
            status=WorkflowRunStatus.new,
            nodes_snapshot=nodes,
            edges_snapshot=edges,
        )
        session.add(run)
        await session.flush()
        nr_ids: dict[str, int] = {}
        for n in nodes:
            nr = NodeRun(
                workflow_run_id=run.id,
                node_key=n["id"],
                node_type="excel_gpt",
                status=NodeRunStatus.pending,
            )
            session.add(nr)
            await session.flush()
            nr_ids[n["id"]] = nr.id
        return project.id, wf.id, nr_ids


def _patch_default_workflow(monkeypatch, workflow_id: int) -> None:
    async def _wf_id(_session=None) -> int:
        return workflow_id

    monkeypatch.setattr("app.services.run_sync._get_default_workflow_id", _wf_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("node_key,label", _OVERFLOW_GROUP)
async def test_start_step_overflow_group_nodes(
    mem_db, tmp_path, monkeypatch, node_key: str, label: str
) -> None:
    """Каждая нода группы «Сценарий → промпты кадров + QC» стартует без KeyError."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    project_id, wf_id, nr_ids = await _seed_overflow_group(mem_db)
    _patch_default_workflow(monkeypatch, wf_id)

    async def _noop_clear(*_a, **_k):
        return {}

    monkeypatch.setattr(
        "app.services.project_steps.clear_step_outputs_for_rerun",
        _noop_clear,
    )
    monkeypatch.setattr(
        "app.services.project_steps.purge_tmp_gpt_for_step",
        lambda *_a, **_k: None,
    )

    async with mem_db() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        status = await start_step(
            session,
            project,
            "excel_gpt",
            node_key=node_key,
            skip_queue_guard=True,
            require_node_fsm=True,
            explicit_ui_start=True,
        )
        assert status is ProjectStatus.enriching_1, label
        assert (project.meta or {}).get("active_excel_gpt_node_key") == node_key

    async with mem_db() as session:
        nr = await session.get(NodeRun, nr_ids[node_key])
        assert nr is not None and nr.status == NodeRunStatus.running, label


@pytest.mark.asyncio
async def test_start_step_after_drop_check_runs_script(
    mem_db, tmp_path, monkeypatch
) -> None:
    """После снятия check ▶ сценариста всё ещё ставит NodeRun running."""
    from app.services import node_groups as ng

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    project_id, wf_id, nr_ids = await _seed_overflow_group(mem_db)
    _patch_default_workflow(monkeypatch, wf_id)

    async def _noop_clear(*_a, **_k):
        return {}

    monkeypatch.setattr(
        "app.services.project_steps.clear_step_outputs_for_rerun",
        _noop_clear,
    )
    monkeypatch.setattr(
        "app.services.project_steps.purge_tmp_gpt_for_step",
        lambda *_a, **_k: None,
    )

    async with mem_db() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        meta = dict(project.meta or {})
        meta["active_excel_gpt_node_key"] = "n_excel_gpt_fw_check_script"
        assert ng.drop_script_frames_qc_check_graph(meta) is True
        project.meta = meta
        status = await start_step(
            session,
            project,
            "excel_gpt",
            node_key="n_excel_gpt_fw_script",
            skip_queue_guard=True,
            require_node_fsm=True,
            explicit_ui_start=True,
        )
        assert status is ProjectStatus.enriching_1
        assert (project.meta or {}).get("active_excel_gpt_node_key") == (
            "n_excel_gpt_fw_script"
        )

    async with mem_db() as session:
        nr = await session.get(NodeRun, nr_ids["n_excel_gpt_fw_script"])
        assert nr is not None and nr.status == NodeRunStatus.running


def test_overflow_successor_after_check_stays_enriching_1() -> None:
    """check → action: slot 0, running = enriching_1, не слот 2."""
    from app.services.excel_gpt_node import (
        first_work_successor_from_excel_slot,
        running_status_for_slot,
    )

    nodes = _overflow_nodes()
    edges = _overflow_edges()
    p = SimpleNamespace(
        id=60,
        status=ProjectStatus.enrich_1_ready,
        meta={"canvas_graph": {"nodes": nodes, "edges": edges}},
    )
    succ = first_work_successor_from_excel_slot(
        p, 1, from_key="n_excel_gpt_fw_check_script"
    )
    assert succ is not None
    key, typ, slot = succ
    assert key == "n_excel_gpt_fw_action"
    assert typ == "excel_gpt"
    assert slot == 0
    assert running_status_for_slot(slot) is ProjectStatus.enriching_1


def test_filter_check_ops_drops_unknown_uuid() -> None:
    from app.orchestrator.steps.enrich_xlsx import _filter_check_frame_ops

    kept, n = _filter_check_frame_ops(
        [
            {"frame_uuid": "4e3c758bdb4f7ca1f75fa6", "fields": {"voiceover_text": "x"}},
            {"frame_uuid": "abc", "fields": {"voiceover_text": "ok"}},
        ],
        ["abc"],
        {},
    )
    assert n == 1
    assert kept == [{"frame_uuid": "abc", "fields": {"voiceover_text": "ok"}}]


@pytest.mark.asyncio
async def test_clear_overflow_slot_only_clears_clicked_node(mem_db) -> None:
    """▶ check не должен сносить excel_gpt_completed_keys сценариста."""
    from app.services.excel_gpt_node import clear_slot_completion_meta

    project_id, _wf_id, _nr = await _seed_overflow_group(mem_db)
    async with mem_db() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        meta = dict(project.meta or {})
        meta["excel_gpt_completed_keys"] = [
            "n_excel_gpt_fw_script",
            "n_excel_gpt_fw_check_script",
        ]
        project.meta = meta
        await clear_slot_completion_meta(
            session,
            project,
            0,
            node_key="n_excel_gpt_fw_check_script",
        )
        keys = project.meta.get("excel_gpt_completed_keys") or []
        assert "n_excel_gpt_fw_script" in keys
        assert "n_excel_gpt_fw_check_script" not in keys


async def _seed_overflow_plus_linear_slot1(scope):
    """Как #63: overflow fw_* + линейный excel_gpt slotIndex=1."""
    overflow = "n_excel_gpt_fw_report"
    leftover = "n_excel_gpt_fw_script"
    slot1 = "n_excel_gpt_1788694559747"
    nodes = [
        {
            "id": leftover,
            "type": "excel_gpt",
            "position": {"x": 0, "y": 0},
            "data": {
                "label": "GPT: сценарист",
                "slotOverflow": True,
                "groupId": "scenariy_prompty_kadrov_qc",
            },
        },
        {
            "id": overflow,
            "type": "excel_gpt",
            "position": {"x": 400, "y": 0},
            "data": {
                "label": "Отчёт",
                "slotOverflow": True,
                "groupId": "scenariy_prompty_kadrov_qc",
            },
        },
        {
            "id": slot1,
            "type": "excel_gpt",
            "position": {"x": 800, "y": 0},
            "data": {"slotIndex": 1, "label": "Персонажи"},
        },
    ]
    slug = f"ovf-steal-{uuid.uuid4().hex[:8]}"
    async with scope() as session:
        wf = Workflow(
            name=f"wf-{uuid.uuid4().hex[:8]}",
            is_default=True,
            nodes=nodes,
            edges=[],
        )
        session.add(wf)
        await session.flush()
        project = Project(
            slug=slug,
            topic="t",
            status=ProjectStatus.enriching_1,
            meta={
                "canvas_graph": {"nodes": nodes, "edges": []},
                "active_excel_gpt_node_key": overflow,
                "excel_gpt_completed_keys": [leftover, overflow],
            },
        )
        session.add(project)
        await session.flush()
        run = WorkflowRun(
            project_id=project.id,
            workflow_id=wf.id,
            status=WorkflowRunStatus.new,
            nodes_snapshot=nodes,
            edges_snapshot=[],
        )
        session.add(run)
        await session.flush()
        nr_ids: dict[str, int] = {}
        for nid, st in (
            (leftover, NodeRunStatus.running),
            (overflow, NodeRunStatus.running),
            (slot1, NodeRunStatus.pending),
        ):
            nr = NodeRun(
                workflow_run_id=run.id,
                node_key=nid,
                node_type="excel_gpt",
                status=st,
            )
            session.add(nr)
            await session.flush()
            nr_ids[nid] = nr.id
        return project.id, wf.id, nr_ids, overflow, leftover, slot1


@pytest.mark.asyncio
async def test_worker_prepare_does_not_steal_overflow_while_enriching(
    mem_db, monkeypatch
) -> None:
    """Лог #63: leftover enriching_1 + completed_keys — не re-resolve на slot 1
    и не pop ключа (иначе воркер заново гоняет GPT). Явный ▶ снимает ключ
    в start_step (test_start_step_pops_overflow_key_then_worker_keeps_it).
    """
    from app.orchestrator.auto_advance import _prepare_node_run_for_status

    project_id, wf_id, nr_ids, overflow, leftover, slot1 = (
        await _seed_overflow_plus_linear_slot1(mem_db)
    )
    _patch_default_workflow(monkeypatch, wf_id)

    async with mem_db() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        ok = await _prepare_node_run_for_status(
            session, project, ProjectStatus.enriching_1, allow_restart=True
        )
        assert ok is False
        assert (project.meta or {}).get("active_excel_gpt_node_key") == overflow
        keys = (project.meta or {}).get("excel_gpt_completed_keys") or []
        # Leftover tick: ключ НЕ снимаем — иначе воркер заново гоняет GPT.
        assert overflow in keys

    async with mem_db() as session:
        assert (await session.get(NodeRun, nr_ids[overflow])).status == (
            NodeRunStatus.running
        )
        assert (await session.get(NodeRun, nr_ids[slot1])).status == (
            NodeRunStatus.pending
        )
        leftover_nr = await session.get(NodeRun, nr_ids[leftover])
        assert leftover_nr is not None
        assert leftover_nr.status in (
            NodeRunStatus.running,
            NodeRunStatus.done,
            NodeRunStatus.pending,
        )


@pytest.mark.asyncio
async def test_prepare_from_ready_overflow_stale_resolves_to_slot1(
    mem_db, monkeypatch
) -> None:
    """Со ready (ещё не running) stale overflow-ключ — re-resolve на slot 1 ок."""
    from app.orchestrator.auto_advance import _prepare_node_run_for_status

    project_id, wf_id, nr_ids, overflow, leftover, slot1 = (
        await _seed_overflow_plus_linear_slot1(mem_db)
    )
    _patch_default_workflow(monkeypatch, wf_id)

    async with mem_db() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        project.status = ProjectStatus.enrich_1_ready
        await session.flush()
        await _prepare_node_run_for_status(
            session, project, ProjectStatus.enriching_1, allow_restart=True
        )
        assert (project.meta or {}).get("active_excel_gpt_node_key") == slot1


@pytest.mark.asyncio
async def test_start_step_pops_overflow_key_then_worker_keeps_it(
    mem_db, tmp_path, monkeypatch
) -> None:
    """▶ уже готового fw_report: ключ снимаем, воркер не прыгает на персонажей."""
    from app.orchestrator.auto_advance import _prepare_node_run_for_status
    from app.services.run_sync import sync_run_for_project

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    project_id, wf_id, nr_ids, overflow, leftover, slot1 = (
        await _seed_overflow_plus_linear_slot1(mem_db)
    )
    _patch_default_workflow(monkeypatch, wf_id)

    async def _noop_clear(*_a, **_k):
        return {}

    monkeypatch.setattr(
        "app.services.project_steps.clear_step_outputs_for_rerun",
        _noop_clear,
    )
    monkeypatch.setattr(
        "app.services.project_steps.purge_tmp_gpt_for_step",
        lambda *_a, **_k: None,
    )

    async with mem_db() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        project.status = ProjectStatus.enrich_1_ready
        status = await start_step(
            session,
            project,
            "excel_gpt",
            node_key=overflow,
            skip_queue_guard=True,
            require_node_fsm=True,
            explicit_ui_start=True,
        )
        assert status is ProjectStatus.enriching_1
        assert (project.meta or {}).get("active_excel_gpt_node_key") == overflow
        keys = (project.meta or {}).get("excel_gpt_completed_keys") or []
        assert overflow not in keys
        assert leftover in keys

    await sync_run_for_project(project_id)

    async with mem_db() as session:
        project = await session.get(Project, project_id)
        assert project is not None
        await _prepare_node_run_for_status(
            session, project, ProjectStatus.enriching_1, allow_restart=True
        )
        assert (project.meta or {}).get("active_excel_gpt_node_key") == overflow

    async with mem_db() as session:
        assert (await session.get(NodeRun, nr_ids[overflow])).status == (
            NodeRunStatus.running
        )
        assert (await session.get(NodeRun, nr_ids[slot1])).status == (
            NodeRunStatus.pending
        )

