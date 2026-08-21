"""Группы нод: каталог + вставка веера scene_design в канвас проекта."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import (
    Base,
    Project,
    ProjectStatus,
    Workflow,
    WorkflowRun,
    WorkflowRunStatus,
)
from app.services import node_groups as ng
from app.services.node_groups import (
    backfill_group_stamps,
    delete_custom_group,
    get_group_detail,
    get_node_group,
    group_from_canvas,
    insert_node_group,
    list_node_groups,
    save_custom_group,
    slugify_group_id,
    update_custom_group,
)


@pytest.fixture(autouse=True)
def _custom_groups_tmp(monkeypatch, tmp_path):
    """Пользовательские группы — в tmp, не в репо (изоляция тестов)."""
    folder = tmp_path / "node_groups"
    monkeypatch.setattr(ng, "custom_groups_dir", lambda: folder)
    return folder


@pytest.fixture
async def mem_db(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
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
    yield _scope
    await engine.dispose()


def _linear_canvas() -> tuple[list[dict], list[dict]]:
    """topic → plan → script → split → hero (позиции как в default_graph)."""
    kinds = ["topic", "plan", "script", "split", "hero"]
    nodes = [
        {
            "id": f"n_{k}",
            "type": k,
            "position": {"x": 80.0 + i * 290.0, "y": 200.0},
            "data": {"label": k},
        }
        for i, k in enumerate(kinds)
    ]
    edges = [
        {"id": f"e_{i}", "source": f"n_{a}", "target": f"n_{b}"}
        for i, (a, b) in enumerate(zip(kinds, kinds[1:], strict=False))
    ]
    return nodes, edges


async def _mk_project(session: AsyncSession, *, with_canvas: bool = True) -> Project:
    nodes, edges = _linear_canvas()
    wf = Workflow(
        name=f"wf-{uuid.uuid4().hex[:8]}",
        is_default=True,
        nodes=nodes,
        edges=edges,
    )
    session.add(wf)
    await session.flush()
    meta: dict = {}
    if with_canvas:
        meta["canvas_graph"] = {
            "workflow_id": wf.id,
            "nodes": nodes,
            "edges": edges,
        }
    project = Project(
        slug=f"grp-{uuid.uuid4().hex[:8]}",
        topic="t",
        status=ProjectStatus.new,
        meta=meta,
    )
    session.add(project)
    await session.flush()
    session.add(
        WorkflowRun(
            project_id=project.id,
            workflow_id=wf.id,
            status=WorkflowRunStatus.new,
            nodes_snapshot=nodes,
            edges_snapshot=edges,
        )
    )
    await session.commit()
    return project


_AGENTS = ("characters", "world", "camera", "action")


def test_catalog_has_scene_fanout() -> None:
    groups = list_node_groups()
    fan = next(g for g in groups if g["id"] == "scene_design_fanout")
    assert fan["category"] == "planning"
    assert fan["node_count"] == 10  # 4 агента + 4 проверки + сборщик + его проверка (стиль удалён)
    assert fan["default_after_type"] == "split"
    keys = {n["key"] for n in fan["nodes"]}
    assert keys == {
        *_AGENTS,
        "assemble",
        "check_asm",
        *(f"check_{a}" for a in _AGENTS),
    }
    grp = get_node_group("scene_design_fanout")
    assert grp is not None
    assert grp.project_meta.get("scene_design_enabled") is True
    assert grp.exit_key == "check_asm"
    assert grp.exit_edge_kind == "pass"
    for spec in grp.nodes:
        if spec.marker:
            # рабочие ноды (агенты + сборщик) — со своими промтами sd_*
            assert spec.prompt_variant and spec.prompt_variant.startswith("sd_")
            assert spec.operator_config is None
        else:
            # проверки — без своего промта, правила с промта источника
            assert spec.prompt_variant is None
            cfg = spec.operator_config
            assert cfg and cfg["checkMode"] is True
            assert cfg["checkFix"] is True
            assert cfg["checkPromptSource"] == "upstream"
            assert spec.slot_overflow is True
    # Топология рёбер: агент → проверка →(Ок)→ сборщик, проверка →(Не ок)→ агент.
    edge_map = {(s, t): k for s, t, k in grp.internal_edges}
    for a in _AGENTS:
        assert edge_map[(a, f"check_{a}")] == "after"
        assert edge_map[(f"check_{a}", "assemble")] == "pass"
        assert edge_map[(f"check_{a}", a)] == "fail"
    assert edge_map[("assemble", "check_asm")] == "after"


async def test_insert_fanout_after_split(mem_db) -> None:
    async with mem_db() as session:
        project = await _mk_project(session)
        res = await insert_node_group(session, project, "scene_design_fanout")

    assert res["after"] == "n_split"
    assert len(res["nodes"]) == 10
    cg = project.meta["canvas_graph"]
    by_id = {n["id"]: n for n in cg["nodes"]}
    split_x = by_id["n_split"]["position"]["x"]
    split_y = by_id["n_split"]["position"]["y"]
    for agent in _AGENTS:
        nid = f"n_excel_gpt_sd_{agent}"
        n = by_id[nid]
        assert n["type"] == "excel_gpt"
        assert n["data"]["sd_agent"] == agent
        assert "slotIndex" not in n["data"]  # веер слоты enrich не занимает
        assert n["position"]["x"] == split_x + 290.0
        assert project.meta["prompt_slot_variants"][nid] == {"main": f"sd_{agent}"}
        # проверка агента — рядом, вне enrich-слотов, с тумблером «Проверка»
        chk = by_id[f"n_excel_gpt_sd_check_{agent}"]
        assert chk["type"] == "excel_gpt"
        assert "sd_agent" not in chk["data"]
        assert chk["data"]["slotOverflow"] is True
        assert chk["position"]["x"] == split_x + 580.0
        assert chk["position"]["y"] == n["position"]["y"]
        cfg = project.meta["excel_gpt_nodes"][f"n_excel_gpt_sd_check_{agent}"]
        assert cfg["checkMode"] is True
        assert cfg["checkFix"] is True
        assert cfg["checkPromptSource"] == "upstream"
        # у проверки нет своего промта — правила с промта агента
        assert f"n_excel_gpt_sd_check_{agent}" not in project.meta["prompt_slot_variants"]
    asm = by_id["n_excel_gpt_sd_asm"]
    assert asm["data"]["sd_agent"] == "assemble"
    assert asm["position"]["x"] == split_x + 870.0
    assert asm["position"]["y"] == split_y
    check_asm = by_id["n_excel_gpt_sd_check_asm"]
    assert check_asm["position"]["x"] == split_x + 1160.0
    assert check_asm["position"]["y"] == split_y
    assert project.meta["excel_gpt_nodes"]["n_excel_gpt_sd_check_asm"]["checkMode"] is True

    pairs = {(e["source"], e["target"]) for e in cg["edges"]}
    assert ("n_split", "n_hero") not in pairs  # перешито
    for agent in _AGENTS:
        assert ("n_split", f"n_excel_gpt_sd_{agent}") in pairs
        assert (f"n_excel_gpt_sd_{agent}", f"n_excel_gpt_sd_check_{agent}") in pairs
        assert (f"n_excel_gpt_sd_check_{agent}", "n_excel_gpt_sd_asm") in pairs
        # петля «Не ок»: проверка → назад агенту
        assert (f"n_excel_gpt_sd_check_{agent}", f"n_excel_gpt_sd_{agent}") in pairs
    assert ("n_excel_gpt_sd_asm", "n_excel_gpt_sd_check_asm") in pairs
    assert ("n_excel_gpt_sd_check_asm", "n_hero") in pairs

    # kind рёбер: «Ок»/«Не ок» как в nicshe.
    kinds = {(e["source"], e["target"]): (e.get("data") or {}).get("kind") for e in cg["edges"]}
    assert kinds[("n_excel_gpt_sd_check_camera", "n_excel_gpt_sd_asm")] == "pass"
    assert kinds[("n_excel_gpt_sd_check_camera", "n_excel_gpt_sd_camera")] == "fail"
    assert kinds[("n_excel_gpt_sd_check_asm", "n_hero")] == "pass"
    labels = {(e["source"], e["target"]): e.get("label") for e in cg["edges"]}
    assert labels[("n_excel_gpt_sd_check_camera", "n_excel_gpt_sd_asm")] == "Ок"
    assert labels[("n_excel_gpt_sd_check_camera", "n_excel_gpt_sd_camera")] == "Не ок"

    assert project.meta["scene_design_enabled"] is True

    # NodeRun'ы созданы с эффективными типами (виртуальные sd_agent/sd_assemble).
    async with mem_db() as session:
        from sqlalchemy import select

        from app.models import NodeRun, WorkflowRun

        run = (
            await session.execute(
                select(WorkflowRun).where(WorkflowRun.project_id == project.id)
            )
        ).scalars().first()
        assert run is not None
        nrs = (
            (
                await session.execute(
                    select(NodeRun).where(NodeRun.workflow_run_id == run.id)
                )
            )
            .scalars()
            .all()
        )
        by_key = {nr.node_key: nr.node_type for nr in nrs}
        assert by_key["n_excel_gpt_sd_camera"] == "sd_agent"
        assert by_key["n_excel_gpt_sd_asm"] == "sd_assemble"


async def test_insert_script_frames_qc_after_plan(mem_db) -> None:
    """Группа «Сценарий → промпты кадров + QC»: вставка после plan, стиль веера."""
    async with mem_db() as session:
        project = await _mk_project(session)
        res = await insert_node_group(session, project, "script_frames_qc")

    assert res["after"] == "n_plan"
    assert len(res["nodes"]) == 4
    cg = project.meta["canvas_graph"]
    by_id = {n["id"]: n for n in cg["nodes"]}
    plan_x = by_id["n_plan"]["position"]["x"]

    script = by_id["n_excel_gpt_fw_script"]
    assert script["type"] == "excel_gpt"
    assert script["position"]["x"] == plan_x + 290.0
    assert script["data"]["groupId"] == "script_frames_qc"
    assert project.meta["prompt_slot_variants"]["n_excel_gpt_fw_script"] == {
        "main": "script_writer_ru"
    }
    # рабочие ноды пишут в DB через apply-ops
    cfg = project.meta["excel_gpt_nodes"]["n_excel_gpt_fw_script"]
    assert cfg["outputMode"] == "project_file"
    assert cfg["transport"] == "api"

    # проверка сценария — как в веере: checkMode, правила с промта источника
    chk = by_id["n_excel_gpt_fw_check_script"]
    assert chk["data"]["slotOverflow"] is True
    ccfg = project.meta["excel_gpt_nodes"]["n_excel_gpt_fw_check_script"]
    assert ccfg["checkMode"] is True
    assert ccfg["checkPromptSource"] == "upstream"
    assert "n_excel_gpt_fw_check_script" not in project.meta["prompt_slot_variants"]

    assert project.meta["prompt_slot_variants"]["n_excel_gpt_fw_frames"] == {
        "main": "frame_prompts_continuity_ru"
    }
    assert project.meta["prompt_slot_variants"]["n_excel_gpt_fw_qc"] == {
        "main": "prompts_qc_continuity_ru"
    }

    pairs = {(e["source"], e["target"]) for e in cg["edges"]}
    assert ("n_plan", "n_script") not in pairs  # нет такого ребра
    assert ("n_plan", "n_excel_gpt_fw_script") in pairs
    assert ("n_excel_gpt_fw_script", "n_excel_gpt_fw_check_script") in pairs
    kinds = {
        (e["source"], e["target"]): (e.get("data") or {}).get("kind")
        for e in cg["edges"]
    }
    assert kinds[("n_excel_gpt_fw_check_script", "n_excel_gpt_fw_frames")] == "pass"
    assert kinds[("n_excel_gpt_fw_check_script", "n_excel_gpt_fw_script")] == "fail"
    assert ("n_excel_gpt_fw_frames", "n_excel_gpt_fw_qc") in pairs
    # выход группы → старая цель plan (script)
    assert ("n_excel_gpt_fw_qc", "n_script") in pairs


async def test_insert_duplicate_raises(mem_db) -> None:
    async with mem_db() as session:
        project = await _mk_project(session)
        await insert_node_group(session, project, "scene_design_fanout")
        with pytest.raises(ValueError, match="уже на канвасе"):
            await insert_node_group(session, project, "scene_design_fanout")


async def test_insert_explicit_after(mem_db) -> None:
    async with mem_db() as session:
        project = await _mk_project(session)
        res = await insert_node_group(
            session, project, "scene_design_fanout", after="n_plan"
        )
    assert res["after"] == "n_plan"
    cg = project.meta["canvas_graph"]
    pairs = {(e["source"], e["target"]) for e in cg["edges"]}
    assert ("n_plan", "n_script") not in pairs
    assert ("n_plan", "n_excel_gpt_sd_characters") in pairs
    assert ("n_excel_gpt_sd_check_asm", "n_script") in pairs


async def test_insert_without_canvas_uses_default_workflow(mem_db) -> None:
    async with mem_db() as session:
        project = await _mk_project(session, with_canvas=False)
        res = await insert_node_group(session, project, "scene_design_fanout")
    assert res["after"] == "n_split"
    cg = project.meta["canvas_graph"]
    assert cg["workflow_id"]  # не 0 — фронт иначе отбросит граф
    assert any(n["id"] == "n_excel_gpt_sd_asm" for n in cg["nodes"])


async def test_insert_replaces_legacy_scene_design_node(mem_db) -> None:
    """Монолит scene_design на канвасе удаляется, рёбра перекидываются мостом."""
    async with mem_db() as session:
        project = await _mk_project(session)
        meta = dict(project.meta)
        cg = dict(meta["canvas_graph"])
        nodes = [dict(n) for n in cg["nodes"]]
        edges = [dict(e) for e in cg["edges"]]
        # split → scene_design → hero вместо split → hero.
        nodes.append(
            {
                "id": "n_scene_design",
                "type": "scene_design",
                "position": {"x": 1500.0, "y": 200.0},
                "data": {"label": "Сцены (агенты)"},
            }
        )
        edges = [e for e in edges if e["id"] != "e_3"]
        edges.append({"id": "e_3a", "source": "n_split", "target": "n_scene_design"})
        edges.append({"id": "e_3b", "source": "n_scene_design", "target": "n_hero"})
        cg["nodes"] = nodes
        cg["edges"] = edges
        meta["canvas_graph"] = cg
        meta["prompt_slot_variants"] = {"n_scene_design": {"main": "old"}}
        project.meta = meta
        await session.commit()

        res = await insert_node_group(session, project, "scene_design_fanout")

    assert res["replaced_nodes"] == ["n_scene_design"]
    cg = project.meta["canvas_graph"]
    by_id = {n["id"]: n for n in cg["nodes"]}
    assert "n_scene_design" not in by_id
    pairs = {(e["source"], e["target"]) for e in cg["edges"]}
    assert not any("n_scene_design" in p for p in pairs)
    assert ("n_split", "n_excel_gpt_sd_camera") in pairs
    assert ("n_excel_gpt_sd_check_asm", "n_hero") in pairs
    assert "n_scene_design" not in project.meta["prompt_slot_variants"]


async def test_insert_stamps_group_id(mem_db) -> None:
    """Вставленные ноды несут штамп группы — обводка на канвасе."""
    async with mem_db() as session:
        project = await _mk_project(session)
        await insert_node_group(session, project, "scene_design_fanout")
    cg = project.meta["canvas_graph"]
    stamped = {
        n["id"]: (n["data"].get("groupId"), n["data"].get("groupTitle"))
        for n in cg["nodes"]
        if n["id"].startswith("n_excel_gpt_sd_")
    }
    assert stamped
    assert all(
        gid == "scene_design_fanout" and title == "Сцены: веер агентов"
        for gid, title in stamped.values()
    )
    # Старые ноды канваса без штампа.
    by_id = {n["id"]: n for n in cg["nodes"]}
    assert "groupId" not in by_id["n_split"]["data"]


def test_custom_group_crud() -> None:
    raw = {
        "group_id": "my_chain",
        "title": "Моя связка",
        "description": "две ноды подряд",
        "category": "enrich",
        "default_after_type": "split",
        "nodes": [
            {"local_key": "a", "node_type": "excel_gpt", "label": "A", "dx": 0, "dy": 0},
            {
                "local_key": "b",
                "node_type": "excel_gpt",
                "label": "B",
                "dx": 290,
                "dy": 0,
                "prompt_variant": "sd_style",
            },
        ],
        "internal_edges": [["a", "b", "after"]],
        "entry_keys": ["a"],
        "exit_key": "b",
    }
    g = save_custom_group(raw)
    assert g.builtin is False
    assert g.updated_at

    listed = {x["id"]: x for x in list_node_groups()}
    assert listed["my_chain"]["builtin"] is False
    assert listed["my_chain"]["node_count"] == 2
    assert listed["scene_design_fanout"]["builtin"] is True

    g2 = update_custom_group(
        "my_chain", {"title": "Переименованная", "category": "media"}
    )
    assert g2.title == "Переименованная"
    assert g2.category == "media"
    assert g2.nodes[1].prompt_variant == "sd_style"  # spec не потёрся

    delete_custom_group("my_chain")
    assert "my_chain" not in {x["id"] for x in list_node_groups()}
    with pytest.raises(ValueError, match="неизвестная группа"):
        delete_custom_group("my_chain")


def test_custom_group_validation_and_builtin_guard() -> None:
    with pytest.raises(ValueError, match="group_id"):
        save_custom_group({"group_id": "!!", "title": "x", "nodes": [{}]})
    with pytest.raises(ValueError, match="title"):
        save_custom_group({"group_id": "ok_id", "nodes": [{"local_key": "a"}]})
    with pytest.raises(ValueError, match="вне nodes"):
        save_custom_group(
            {
                "group_id": "ok_id",
                "title": "x",
                "nodes": [{"local_key": "a"}],
                "internal_edges": [["a", "b", "after"]],
            }
        )
    # id встроенной группы занят.
    with pytest.raises(ValueError, match="встроенной"):
        save_custom_group(
            {
                "group_id": "scene_design_fanout",
                "title": "x",
                "nodes": [{"local_key": "a"}],
            }
        )
    with pytest.raises(ValueError, match="встроенная"):
        update_custom_group("scene_design_fanout", {"title": "y"})
    with pytest.raises(ValueError, match="встроенная"):
        delete_custom_group("scene_design_fanout")


def test_slugify_group_id() -> None:
    assert slugify_group_id("Моя связка GPT") == "moya_svyazka_gpt"
    assert slugify_group_id("  ") .startswith("group_")
    assert slugify_group_id("Chain 2/проверка") == "chain_2_proverka"


def test_group_detail_spec() -> None:
    """Детальный spec группы для превью: позиции, рёбра, промты, входы/выход."""
    d = get_group_detail("scene_design_fanout")
    assert d is not None
    assert d["builtin"] is True
    assert d["exit_key"] == "check_asm"
    assert set(d["entry_keys"]) == {
        "characters", "world", "camera", "action"
    }
    by_key = {n["key"]: n for n in d["nodes"]}
    cam = by_key["camera"]
    assert cam["prompt_variant"] == "sd_camera"
    assert cam["marker"] == "camera"
    assert cam["dx"] == 290.0
    chk = by_key["check_camera"]
    assert chk["slot_overflow"] is True
    assert chk["has_operator_config"] is True
    kinds = {(e["source"], e["target"]): e["kind"] for e in d["internal_edges"]}
    assert kinds[("check_camera", "assemble")] == "pass"
    assert kinds[("check_camera", "camera")] == "fail"
    assert get_group_detail("nope") is None


async def test_backfill_group_stamps(mem_db) -> None:
    """Старый проект (веер без штампов) → бэкфилл проставляет groupId."""
    async with mem_db() as session:
        project = await _mk_project(session)
        await insert_node_group(session, project, "scene_design_fanout")
        # Симулируем проект «до штампов»: снимаем groupId/groupTitle.
        meta = dict(project.meta)
        for n in meta["canvas_graph"]["nodes"]:
            n.get("data", {}).pop("groupId", None)
            n.get("data", {}).pop("groupTitle", None)
        project.meta = meta
        await session.commit()

        stats = await backfill_group_stamps(session)

    assert stats["nodes"] == 10  # 4 агента + сборщик + 5 проверок (стиль удалён)
    assert stats["projects"] == 1
    cg = project.meta["canvas_graph"]
    stamped = [
        n
        for n in cg["nodes"]
        if (n.get("data") or {}).get("groupId") == "scene_design_fanout"
    ]
    assert len(stamped) == 10
    assert all(
        n["data"]["groupTitle"] == "Сцены: веер агентов" for n in stamped
    )
    # Посторонние ноды не тронуты.
    by_id = {n["id"]: n for n in cg["nodes"]}
    assert "groupId" not in by_id["n_split"]["data"]

    # Идемпотентно: повторный прогон ничего не меняет.
    async with mem_db() as session:
        stats2 = await backfill_group_stamps(session)
    assert stats2 == {"projects": 0, "nodes": 0}


async def test_group_from_canvas_and_reinsert(mem_db) -> None:
    """Выделение → группа (промты/конфиги/связи) → вставка в другой проект."""
    async with mem_db() as session:
        project = await _mk_project(session)
        meta = dict(project.meta)
        cg = dict(meta["canvas_graph"])
        nodes = [dict(n) for n in cg["nodes"]]
        edges = [dict(e) for e in cg["edges"]]
        # split → g1 → g2 → hero: g1/g2 — будущая группа.
        nodes.append(
            {
                "id": "n_g1",
                "type": "excel_gpt",
                "position": {"x": 1500.0, "y": 200.0},
                "data": {"label": "GPT A", "description": "первая"},
            }
        )
        nodes.append(
            {
                "id": "n_g2",
                "type": "excel_gpt",
                "position": {"x": 1790.0, "y": 220.0},
                "data": {"label": "GPT B", "slotOverflow": True},
            }
        )
        edges = [e for e in edges if e["id"] != "e_3"]
        edges.append({"id": "e_3a", "source": "n_split", "target": "n_g1"})
        edges.append(
            {
                "id": "e_3b",
                "source": "n_g1",
                "target": "n_g2",
                "data": {"kind": "pass"},
            }
        )
        edges.append({"id": "e_3c", "source": "n_g2", "target": "n_hero"})
        cg["nodes"] = nodes
        cg["edges"] = edges
        meta["canvas_graph"] = cg
        meta["prompt_slot_variants"] = {"n_g1": {"main": "sd_world"}}
        meta["excel_gpt_nodes"] = {"n_g2": {"checkMode": True, "outputMode": "text"}}
        project.meta = meta
        await session.commit()

        group = await group_from_canvas(
            session,
            project,
            ["n_g1", "n_g2"],
            title="Моя связка",
            description="описание",
            category="enrich",
        )

    assert group.group_id == "moya_svyazka"
    assert group.builtin is False
    assert group.default_after_type == "split"  # внешний вход от split
    assert group.entry_keys == ("n_g1",)
    assert group.exit_key == "n_g2"
    assert group.internal_edges == (("n_g1", "n_g2", "pass"),)
    by_key = {s.local_key: s for s in group.nodes}
    assert by_key["n_g1"].prompt_variant == "sd_world"
    assert by_key["n_g1"].dx == 0.0
    assert by_key["n_g2"].dx == 290.0
    assert by_key["n_g2"].slot_overflow is True
    assert by_key["n_g2"].operator_config == {"checkMode": True, "outputMode": "text"}

    # Вставка в другой проект: промты/конфиги/штампы на месте.
    async with mem_db() as session:
        other = await _mk_project(session)
        res = await insert_node_group(session, other, "moya_svyazka")

    assert res["after"] == "n_split"
    cg = other.meta["canvas_graph"]
    by_id = {n["id"]: n for n in cg["nodes"]}
    assert by_id["n_g1"]["data"]["groupId"] == "moya_svyazka"
    assert by_id["n_g1"]["data"]["groupTitle"] == "Моя связка"
    assert by_id["n_g2"]["data"]["slotOverflow"] is True
    assert other.meta["prompt_slot_variants"]["n_g1"] == {"main": "sd_world"}
    assert other.meta["excel_gpt_nodes"]["n_g2"]["checkMode"] is True
    kinds = {
        (e["source"], e["target"]): (e.get("data") or {}).get("kind")
        for e in cg["edges"]
    }
    assert kinds[("n_g1", "n_g2")] == "pass"
    assert ("n_split", "n_g1") in kinds
    assert ("n_g2", "n_hero") in kinds

    # Повторная вставка — разрешена, копия с instance-id «#2».
    async with mem_db() as session:
        project2 = await session.get(type(other), other.id)
        res2 = await insert_node_group(session, project2, "moya_svyazka")
    cg2 = project2.meta["canvas_graph"]
    copies = [
        n["data"]["groupId"]
        for n in cg2["nodes"]
        if (n.get("data") or {}).get("groupId")
    ]
    assert sorted(set(copies)) == ["moya_svyazka", "moya_svyazka#2"]
    assert res2["nodes"] != res["nodes"]  # id с суффиксами
