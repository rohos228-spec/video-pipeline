"""Группы нод (пресеты канваса): веер scene_design и будущие связки.

Определения живут в репозитории — наследуются всеми ПК через git pull.
Группа = набор нод (тип, data-маркеры, промпт-вариант, относительные
позиции) + внутренние связи + точки входа/выхода для вшивания в цепочку
существующего канваса. Результаты работ (чекпоинты, ячейки, реестры) в
группу не входят — только структура, промты и флаги meta.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.orchestrator.default_graph import SD_FANOUT
from app.services.canvas_graph import (
    build_canvas_graph_payload,
    canvas_graph_from_meta,
    sync_run_snapshot_from_canvas_graph,
)
from app.services.excel_gpt_node import sd_agent_marker

_STEP_X = 290.0  # как в default_graph
_FAN_DY = 145.0


@dataclass(frozen=True)
class GroupNodeSpec:
    """Нода внутри группы (локальный ключ → параметры)."""

    local_key: str  # "characters" / "assemble" — локальный id в группе
    node_type: str  # тип ноды на канвасе (excel_gpt и т.п.)
    label: str
    description: str
    preferred_id: str  # желаемый node id (n_excel_gpt_sd_characters)
    dx: float  # смещение от якоря вставки (колонка)
    dy: float  # смещение по вертикали относительно центра группы
    marker: str | None = None  # data.sd_agent
    prompt_variant: str | None = None  # файл в 05_excel_gpt (sd_<агент>)
    slot_overflow: bool = False  # data.slotOverflow — вне enrich-слотов 1..5
    # Конфиг «Работы с GPT» → meta.excel_gpt_nodes[node_id] (проверки и т.п.)
    operator_config: dict[str, Any] | None = None


@dataclass(frozen=True)
class NodeGroupDef:
    """Пресет группы нод для палитры «+ Группа»."""

    group_id: str
    title: str
    description: str
    category: str  # planning / objects / enrich / media / audio / assembly / publish
    default_after_type: str  # после ноды этого типа вшивать по умолчанию
    nodes: tuple[GroupNodeSpec, ...]
    # (local src, local tgt, kind): kind = after|pass|fail (pass=«Ок», fail=«Не ок»)
    internal_edges: tuple[tuple[str, str, str], ...]
    entry_keys: tuple[str, ...]  # локальные ключи нод, принимающих внешний вход
    exit_key: str  # локальный ключ ноды-выхода
    project_meta: dict[str, Any] = field(default_factory=dict)
    # Типы нод, которые группа ЗАМЕНЯЕТ на канвасе (монолит scene_design
    # удаляется при вставке веера, pipeline-рёбра перекидываются мостом).
    replaces_types: tuple[str, ...] = ()
    # kind рёбер от выхода группы к старым целям якоря («pass» — через вердикт).
    exit_edge_kind: str = "after"


# Конфиг ноды проверки — как в эталонном проекте «nicshe» (#50):
# тумблер «Проверка» + «Чинить», правила — промт ноды-источника (upstream).
_CHECK_OPERATOR_CONFIG: dict[str, Any] = {
    "outputMode": "text",
    "emitKinds": ["inputs", "reply_txt"],
    "checkMode": True,
    "checkFix": True,
    "checkPromptSource": "upstream",
    "transport": "api",
}


def _check_spec(local_key: str, label: str, descr: str, dx: float, dy: float) -> GroupNodeSpec:
    return GroupNodeSpec(
        local_key=local_key,
        node_type="excel_gpt",
        label=label,
        description=descr,
        preferred_id=f"n_excel_gpt_sd_{local_key}",
        dx=dx,
        dy=dy,
        slot_overflow=True,  # проверки не занимают enrich-слоты 1..5
        operator_config=dict(_CHECK_OPERATOR_CONFIG),
    )


def _scene_design_group() -> NodeGroupDef:
    agents: list[GroupNodeSpec] = []
    checks: list[GroupNodeSpec] = []
    edges: list[tuple[str, str, str]] = []
    mid = (len(SD_FANOUT) - 1) / 2
    for i, (agent, label, descr) in enumerate(SD_FANOUT):
        dy = (i - mid) * _FAN_DY
        agents.append(
            GroupNodeSpec(
                local_key=agent,
                node_type="excel_gpt",
                label=label,
                description=descr,
                preferred_id=f"n_excel_gpt_sd_{agent}",
                dx=_STEP_X,
                dy=dy,
                marker=agent,
                prompt_variant=f"sd_{agent}",
            )
        )
        check_key = f"check_{agent}"
        short = label.removeprefix("GPT: ")
        checks.append(
            _check_spec(
                check_key,
                f"Проверка: {short}",
                f"Проверка ответа агента «{short}» по его промту (как в nicshe)",
                dx=_STEP_X * 2,
                dy=dy,
            )
        )
        # агент → своя проверка; «Ок» → сборщик; «Не ок» → назад агенту.
        edges.append((agent, check_key, "after"))
        edges.append((check_key, "assemble", "pass"))
        edges.append((check_key, agent, "fail"))
    asm = GroupNodeSpec(
        local_key="assemble",
        node_type="excel_gpt",
        label="GPT: сборка сцен",
        description="Финальный агент-сборщик: ячейки → scene_registry + attrs кадров",
        preferred_id="n_excel_gpt_sd_asm",
        dx=_STEP_X * 3,
        dy=0.0,
        marker="assemble",
        prompt_variant="sd_assemble",
    )
    check_asm = _check_spec(
        "check_asm",
        "Проверка: сборка сцен",
        "Проверка scene_registry и attrs кадров по промту сборщика",
        dx=_STEP_X * 4,
        dy=0.0,
    )
    edges.append(("assemble", "check_asm", "after"))
    return NodeGroupDef(
        group_id="scene_design_fanout",
        title="Сцены: веер агентов",
        description=(
            "5 GPT-агентов (персонажи/мир/стиль/камера/действие) параллельно "
            "+ сборщик сцен; после каждой ноды — её проверка (Ок/Не ок). "
            "Промты sd_* из 05_excel_gpt выставляются сразу."
        ),
        category="planning",
        default_after_type="split",
        nodes=(*agents, *checks, asm, check_asm),
        internal_edges=tuple(edges),
        entry_keys=tuple(a for a, _l, _d in SD_FANOUT),
        exit_key="check_asm",
        project_meta={"scene_design_enabled": True},
        replaces_types=("scene_design",),
        exit_edge_kind="pass",
    )


NODE_GROUPS: dict[str, NodeGroupDef] = {
    g.group_id: g for g in (_scene_design_group(),)
}


def list_node_groups() -> list[dict[str, Any]]:
    """Каталог групп для палитры (API)."""
    return [
        {
            "id": g.group_id,
            "title": g.title,
            "description": g.description,
            "category": g.category,
            "node_count": len(g.nodes),
            "nodes": [
                {"key": n.local_key, "label": n.label, "type": n.node_type}
                for n in g.nodes
            ],
            "default_after_type": g.default_after_type,
        }
        for g in NODE_GROUPS.values()
    ]


def get_node_group(group_id: str) -> NodeGroupDef | None:
    return NODE_GROUPS.get(str(group_id or "").strip())


def _side_sink_ids(nodes: list[dict]) -> set[str]:
    """Ноды-приёмники «сбоку» (storage/topic/excel_feed) — не цепочка."""
    from app.orchestrator.node_registry import CONFIG_NODE_TYPES

    side = set(CONFIG_NODE_TYPES) | {"excel_feed"}
    return {
        str(n.get("id"))
        for n in nodes
        if n.get("id") and str(n.get("type") or "") in side
    }


def _pipeline_targets(nodes: list[dict], edges: list[dict], src: str) -> list[str]:
    """Цели pipeline-рёбер из src (без веток на storage/topic/excel_feed)."""
    sinks = _side_sink_ids(nodes)
    return [
        str(e.get("target"))
        for e in edges
        if str(e.get("source")) == src and str(e.get("target")) not in sinks
    ]


def _wire_to_storage(nodes: list[dict], edges: list[dict], new_ids: list[str]) -> int:
    """Дотянуть новые ноды к первому storage (как add_node в db_browser)."""
    stor = next(
        (str(n.get("id")) for n in nodes if str(n.get("type") or "") == "storage"),
        None,
    )
    if not stor:
        return 0
    have = {(str(e.get("source")), str(e.get("target"))) for e in edges}
    added = 0
    for nid in new_ids:
        if (nid, stor) in have:
            continue
        edges.append(
            {
                "id": f"e_{nid}_{stor}",
                "source": nid,
                "target": stor,
                "data": {"kind": "after"},
            }
        )
        added += 1
    return added


async def insert_node_group(
    session: AsyncSession,
    project: Project,
    group_id: str,
    *,
    after: str | None = None,
) -> dict[str, Any]:
    """Вшить группу нод в канвас проекта: позиции, связи, промты, флаги.

    Якорь: явный ``after`` (node_key) → иначе первая нода типа
    ``default_after_type`` → иначе хвост цепочки. Исходящие pipeline-рёбра
    якоря перешиваются: якорь → входы группы → выход → старые цели.
    """
    group = get_node_group(group_id)
    if group is None:
        raise ValueError(
            f"неизвестная группа {group_id!r}; есть: {sorted(NODE_GROUPS)}"
        )

    from sqlalchemy import select

    from app.models import Workflow

    meta = dict(project.meta or {})
    graph = canvas_graph_from_meta(meta)
    workflow_id: int | None = None
    if graph is None:
        wf = (
            await session.execute(
                select(Workflow).where(Workflow.is_default.is_(True))
            )
        ).scalars().first()
        if wf is None:
            raise ValueError("insert_group: нет ни canvas_graph, ни default workflow")
        nodes = [dict(n) for n in (wf.nodes or [])]
        edges = [dict(e) for e in (wf.edges or [])]
        workflow_id = wf.id
    else:
        workflow_id = graph.get("workflow_id")
        nodes = [dict(n) for n in graph["nodes"]]
        edges = [dict(e) for e in graph["edges"]]
    if not workflow_id:
        # Фронт отбрасывает canvas_graph с чужим/нулевым workflow_id.
        wf = (
            await session.execute(
                select(Workflow).where(Workflow.is_default.is_(True))
            )
        ).scalars().first()
        workflow_id = wf.id if wf else None

    by_id = {str(n.get("id")): n for n in nodes}

    # Замена устаревших типов (монолит scene_design → веер): ноды убираем,
    # pipeline-рёбра перекидываем мостом вход→выход.
    removed_replaced: list[str] = []
    if group.replaces_types:
        replace_ids = {
            str(n.get("id"))
            for n in nodes
            if str(n.get("type") or "") in group.replaces_types
        }
        if replace_ids:
            incoming: dict[str, list[str]] = {rid: [] for rid in replace_ids}
            outgoing: dict[str, list[str]] = {rid: [] for rid in replace_ids}
            kept: list[dict] = []
            for e in edges:
                src, tgt = str(e.get("source")), str(e.get("target"))
                if tgt in replace_ids:
                    if src not in replace_ids:
                        incoming[tgt].append(src)
                    continue
                if src in replace_ids:
                    if tgt not in replace_ids:
                        outgoing[src].append(tgt)
                    continue
                kept.append(e)
            for rid in replace_ids:
                for src in incoming[rid]:
                    for tgt in outgoing[rid]:
                        kept.append(
                            {
                                "id": f"e_{src}_{tgt}",
                                "source": src,
                                "target": tgt,
                                "sourceHandle": "out",
                                "targetHandle": "in",
                            }
                        )
            edges = kept
            nodes = [n for n in nodes if str(n.get("id")) not in replace_ids]
            by_id = {str(n.get("id")): n for n in nodes}
            removed_replaced = sorted(replace_ids)

    # Дубль-защита: маркеры группы уже на канвасе.
    group_markers = {n.marker for n in group.nodes if n.marker}
    existing_markers = {m for n in nodes if (m := sd_agent_marker(n))}
    overlap = group_markers & existing_markers
    if overlap:
        raise ValueError(
            f"группа «{group.title}» уже на канвасе (маркеры: {sorted(overlap)})"
        )

    # Якорь вставки.
    anchor_id: str | None = None
    if after:
        anchor_id = str(after).strip()
        if anchor_id not in by_id:
            raise ValueError(f"insert_group: неизвестный after {anchor_id!r}")
    else:
        for n in nodes:
            if str(n.get("type") or "") == group.default_after_type:
                anchor_id = str(n.get("id"))
                break
        if anchor_id is None:
            # Хвост цепочки: work-нода без исходящих pipeline-рёбер.
            from app.orchestrator.node_registry import is_work_node_type

            for n in reversed(nodes):
                nid = str(n.get("id") or "")
                if not nid or not is_work_node_type(str(n.get("type") or "")):
                    continue
                if not _pipeline_targets(nodes, edges, nid):
                    anchor_id = nid
                    break
    if anchor_id is None:
        raise ValueError("insert_group: не нашёл точку вставки на канвасе")

    anchor = by_id[anchor_id]
    ax = float((anchor.get("position") or {}).get("x", 0.0))
    ay = float((anchor.get("position") or {}).get("y", 0.0))

    # Уникальные id (preferred или с суффиксом).
    used = set(by_id)
    local_to_id: dict[str, str] = {}
    for spec in group.nodes:
        nid = spec.preferred_id
        k = 2
        while nid in used:
            nid = f"{spec.preferred_id}_{k}"
            k += 1
        used.add(nid)
        local_to_id[spec.local_key] = nid

    new_nodes: list[dict] = []
    for spec in group.nodes:
        data: dict[str, Any] = {"label": spec.label, "description": spec.description}
        if spec.marker:
            data["sd_agent"] = spec.marker
        if spec.slot_overflow:
            data["slotOverflow"] = True
        new_nodes.append(
            {
                "id": local_to_id[spec.local_key],
                "type": spec.node_type,
                "position": {"x": ax + spec.dx, "y": ay + spec.dy},
                "data": data,
            }
        )

    # Перешивка: старые pipeline-цели якоря → за выход группы.
    old_targets = _pipeline_targets(nodes, edges, anchor_id)
    out_edges: list[dict] = [
        e
        for e in edges
        if not (
            str(e.get("source")) == anchor_id
            and str(e.get("target")) in old_targets
        )
    ]

    _EDGE_LABELS = {"pass": "Ок", "fail": "Не ок"}

    def _edge(src: str, tgt: str, eid: str, kind: str = "after") -> dict:
        e: dict[str, Any] = {
            "id": eid,
            "source": src,
            "target": tgt,
            "sourceHandle": "out",
            "targetHandle": "in",
            "data": {"kind": kind},
        }
        label = _EDGE_LABELS.get(kind)
        if label:
            e["label"] = label
            e["data"]["label"] = label
        return e

    for key in group.entry_keys:
        out_edges.append(
            _edge(anchor_id, local_to_id[key], f"e_{anchor_id}_{local_to_id[key]}")
        )
    for src_key, tgt_key, kind in group.internal_edges:
        out_edges.append(
            _edge(
                local_to_id[src_key],
                local_to_id[tgt_key],
                f"e_{local_to_id[src_key]}_{local_to_id[tgt_key]}",
                kind,
            )
        )
    exit_id = local_to_id[group.exit_key]
    for tgt in old_targets:
        out_edges.append(
            _edge(exit_id, tgt, f"e_{exit_id}_{tgt}", group.exit_edge_kind)
        )

    all_nodes = nodes + new_nodes
    new_ids = [n["id"] for n in new_nodes]
    wired = _wire_to_storage(all_nodes, out_edges, new_ids)

    # Промпт-варианты нод (SSoT — meta.prompt_slot_variants, как у всех GPT-нод).
    variants = meta.get("prompt_slot_variants")
    variants = dict(variants) if isinstance(variants, dict) else {}
    for rid in removed_replaced:
        variants.pop(rid, None)
    for spec in group.nodes:
        if spec.prompt_variant:
            variants[local_to_id[spec.local_key]] = {"main": spec.prompt_variant}
    meta["prompt_slot_variants"] = variants

    # Конфиги «Работы с GPT» (тумблер «Проверка» у check-нод и т.п.).
    # У проверок промта в variants нет — правила тянутся с промта источника
    # (checkPromptSource=upstream), как в эталонном «nicshe».
    egn = meta.get("excel_gpt_nodes")
    egn = dict(egn) if isinstance(egn, dict) else {}
    for rid in removed_replaced:
        egn.pop(rid, None)
    for spec in group.nodes:
        if spec.operator_config:
            egn[local_to_id[spec.local_key]] = dict(spec.operator_config)
    meta["excel_gpt_nodes"] = egn

    # Флаги проекта из группы (например scene_design_enabled).
    for k, v in group.project_meta.items():
        meta[k] = v

    meta["canvas_graph"] = build_canvas_graph_payload(
        workflow_id=int(workflow_id or 0),
        nodes=all_nodes,
        edges=out_edges,
    )
    project.meta = meta
    await session.flush()
    await sync_run_snapshot_from_canvas_graph(session, project, force=True)
    await session.commit()

    logger.info(
        "[#{}] insert_group {}: +{} нод после {}, рёбер→storage: {}",
        project.id,
        group.group_id,
        len(new_nodes),
        anchor_id,
        wired,
    )
    return {
        "group": group.group_id,
        "after": anchor_id,
        "nodes": new_ids,
        "replaced_nodes": removed_replaced,
        "edges_added": len(group.entry_keys)
        + len(group.internal_edges)
        + len(old_targets)
        + wired,
        "prompt_variants": {
            local_to_id[s.local_key]: s.prompt_variant
            for s in group.nodes
            if s.prompt_variant
        },
        "check_nodes": [
            local_to_id[s.local_key] for s in group.nodes if s.operator_config
        ],
        "project_meta": dict(group.project_meta),
    }
