"""Учёт отключённых нод workflow в meta.disabled_nodes (ключи n_*)."""

from __future__ import annotations

from app.models import Project, ProjectStatus

from app.orchestrator.node_registry import (
    LINEAR_RUNNING_PIPELINE,
    STEP_CODE_TO_NODE_TYPE,
)

KNOWN_NODE_TYPES: tuple[str, ...] = tuple(
    sorted(set(STEP_CODE_TO_NODE_TYPE.values()), key=len, reverse=True)
)

RUNNING_PIPELINE = LINEAR_RUNNING_PIPELINE


def node_type_from_key(node_key: str) -> str | None:
    """n_plan → plan, n_plan_1700000000 → plan, n_enrich_1 → enrich_1."""
    key = (node_key or "").strip()
    if not key:
        return None
    if not key.startswith("n_"):
        return key if key in KNOWN_NODE_TYPES else None
    rest = key[2:]
    if rest in KNOWN_NODE_TYPES:
        return rest
    for typ in KNOWN_NODE_TYPES:
        if rest == typ or rest.startswith(f"{typ}_"):
            return typ
    return None


def disabled_node_types(project: Project) -> set[str]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    keys = meta.get("disabled_nodes") or []
    out: set[str] = set()
    for k in keys:
        t = node_type_from_key(str(k))
        if t:
            out.add(t)
    return out


def step_code_to_node_type(step_code: str) -> str | None:
    return STEP_CODE_TO_NODE_TYPE.get(step_code)


def is_step_disabled(project: Project, step_code: str) -> bool:
    typ = step_code_to_node_type(step_code)
    if typ is None:
        return False
    return typ in disabled_node_types(project)


def is_node_type_disabled(project: Project, node_type: str) -> bool:
    return node_type in disabled_node_types(project)


def skip_disabled_running(
    project: Project,
    target: ProjectStatus | None,
) -> ProjectStatus | None:
    """Если running-шаг отключён — перейти к следующему включённому (граф или линейно)."""
    from app.orchestrator.graph.planner import WorkflowGraph, graph_executor_enabled

    if target is None:
        return None
    if graph_executor_enabled(project):
        # Без session нельзя загрузить snapshot — не подменяем дефолтным графом.
        return target
    disabled = disabled_node_types(project)
    if not disabled:
        return target
    start_idx = 0
    for i, (st, _) in enumerate(RUNNING_PIPELINE):
        if st == target:
            start_idx = i
            break
    for st, typ in RUNNING_PIPELINE[start_idx:]:
        if typ not in disabled:
            return st
    return None


async def skip_disabled_running_async(
    session,
    project: Project,
    target: ProjectStatus | None,
) -> ProjectStatus | None:
    """Graph-aware skip с загрузкой snapshot проекта."""
    from app.orchestrator.graph.planner import (
        WorkflowGraph,
        graph_executor_enabled,
        load_graph_for_project,
    )

    if target is None:
        return None
    if not graph_executor_enabled(project):
        return skip_disabled_running(project, target)
    graph = await load_graph_for_project(session, project)
    return graph.skip_disabled_running(project, target)
