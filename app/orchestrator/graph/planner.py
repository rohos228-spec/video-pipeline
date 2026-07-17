"""Планировщик графа: edges + disabled_nodes → следующий шаг и статусы NodeRun."""

from __future__ import annotations

from collections import deque
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import NodeRunStatus, Project, ProjectStatus, WorkflowRun
from app.orchestrator.node_registry import (
    NODE_TYPE_TO_RUNNING,
    READY_TO_NODE_TYPE,
    RUNNING_TO_NODE_TYPE,
    is_config_node_type,
    is_hitl_node_type,
    is_work_node_type,
    spec_for_node,
    spec_for_step_code,
    spec_for_type,
)
from app.services.excel_gpt_node import (
    EXCEL_GPT_NODE_TYPE,
    completed_node_keys,
    ready_status_for_slot,
    running_status_for_slot,
    slot_from_running_status,
    slot_index_from_node,
)
from app.services.disabled_nodes import disabled_node_types

PASSTHROUGH_NODE_TYPES: frozenset[str] = frozenset({"excel_feed"})


def is_passthrough_node_type(node_type: str) -> bool:
    return (
        is_hitl_node_type(node_type)
        or is_config_node_type(node_type)
        or node_type in PASSTHROUGH_NODE_TYPES
    )


class WorkflowGraph:
    """In-memory view of workflow nodes/edges."""

    def __init__(self, nodes: list[dict[str, Any]], edges: list[dict[str, Any]]) -> None:
        self.nodes = list(nodes or [])
        self.edges = list(edges or [])
        self._by_id: dict[str, dict[str, Any]] = {n["id"]: n for n in self.nodes if "id" in n}
        self._out: dict[str, list[str]] = {nid: [] for nid in self._by_id}
        self._in: dict[str, list[str]] = {nid: [] for nid in self._by_id}
        for e in self.edges:
            src, tgt = e.get("source"), e.get("target")
            if src in self._out and tgt in self._in:
                self._out[src].append(tgt)
                self._in[tgt].append(src)

    @classmethod
    def default(cls) -> WorkflowGraph:
        from app.orchestrator.default_graph import default_graph

        nodes, edges = default_graph()
        return cls(nodes, edges)

    def node_type(self, node_key: str) -> str:
        n = self._by_id.get(node_key) or {}
        return str(n.get("type") or "")

    def keys_of_type(self, node_type: str) -> list[str]:
        return [nid for nid, n in self._by_id.items() if n.get("type") == node_type]

    def excel_gpt_keys_for_slot(self, slot: int) -> list[str]:
        return [
            nid
            for nid, n in self._by_id.items()
            if str(n.get("type") or "") == EXCEL_GPT_NODE_TYPE
            and slot_index_from_node(n) == slot
        ]

    def skipped_keys(self, project: Project) -> set[str]:
        disabled_types = disabled_node_types(project)
        out: set[str] = set()
        meta = project.meta if isinstance(project.meta, dict) else {}
        for key in meta.get("disabled_nodes") or []:
            out.add(str(key))
        for nid, n in self._by_id.items():
            typ = str(n.get("type") or "")
            if typ in disabled_types:
                out.add(nid)
        return out

    def _effective_predecessors(self, node_key: str, skipped: set[str]) -> set[str]:
        """Предшественники с учётом пропуска hitl/disabled (они прозрачны)."""
        result: set[str] = set()
        stack = list(self._in.get(node_key, []))
        seen: set[str] = set()
        while stack:
            cur = stack.pop()
            if cur in seen:
                continue
            seen.add(cur)
            if cur in skipped:
                stack.extend(self._in.get(cur, []))
                continue
            typ = self.node_type(cur)
            if is_passthrough_node_type(typ):
                stack.extend(self._in.get(cur, []))
                continue
            result.add(cur)
        return result

    def _flow_work_keys(self, skipped: set[str]) -> set[str]:
        """Рабочие ноды, достижимые от topic/excel_feed и от «входных» без предшественников."""
        roots: list[str] = []
        for nid, n in self._by_id.items():
            typ = str(n.get("type") or "")
            if is_config_node_type(typ) or typ in PASSTHROUGH_NODE_TYPES:
                roots.append(nid)
        for nid in self._by_id:
            if nid in skipped:
                continue
            typ = self.node_type(nid)
            if is_work_node_type(typ) and not self._effective_predecessors(nid, skipped):
                if nid not in roots:
                    roots.append(nid)

        reachable: set[str] = set()
        queue: deque[str] = deque(roots)
        seen: set[str] = set()
        while queue:
            key = queue.popleft()
            if key in seen:
                continue
            seen.add(key)
            typ = self.node_type(key)
            if key not in skipped and is_work_node_type(typ):
                reachable.add(key)
            queue.extend(self._out.get(key, []))
        return reachable

    def _work_types_done(self, project: Project) -> set[str]:
        """Какие рабочие типы уже завершены по Project.status (только по графу канваса)."""
        status = project.status
        done: set[str] = set()
        skipped = self.skipped_keys(project)

        if status in READY_TO_NODE_TYPE:
            cur = READY_TO_NODE_TYPE[status]
            done.add(cur)
            for key in self.keys_of_type(cur):
                for p in self._effective_predecessors(key, skipped):
                    done.add(self.node_type(p))
        elif status in RUNNING_TO_NODE_TYPE:
            cur = RUNNING_TO_NODE_TYPE[status]
            for key in self.keys_of_type(cur):
                for p in self._effective_predecessors(key, skipped):
                    done.add(self.node_type(p))
        elif status is ProjectStatus.published:
            for key in self._flow_work_keys(skipped):
                done.add(self.node_type(key))
        meta = project.meta if isinstance(project.meta, dict) else {}
        # enrich_completed_slots учитываем только после реального split
        # (split_completed) или когда уже внутри enrich-зоны. Иначе stale
        # meta на frames_ready пропускает excel_gpt #1/#2.
        from app.telegram.menu import status_order as _status_ord

        trust_enrich_meta = bool(meta.get("split_completed")) or (
            status is not None
            and _status_ord(status) >= _status_ord(ProjectStatus.enriching_1)
        )
        if trust_enrich_meta:
            for slot in meta.get("enrich_completed_slots") or []:
                try:
                    idx = int(slot)
                except (TypeError, ValueError):
                    continue
                if 1 <= idx <= 5:
                    done.add(f"enrich_{idx}")
        return done

    def _is_ready(self, node_key: str, project: Project, skipped: set[str]) -> bool:
        if node_key in skipped:
            return True
        typ = self.node_type(node_key)
        if is_hitl_node_type(typ):
            preds = self._effective_predecessors(node_key, skipped)
            done = self._work_types_done(project)
            return all(self.node_type(p) in done for p in preds)
        if is_config_node_type(typ):
            return bool((project.topic or "").strip())
        if not is_work_node_type(typ):
            return True
        done = self._work_types_done(project)
        if typ == EXCEL_GPT_NODE_TYPE:
            n = self._by_id.get(node_key) or {}
            slot = slot_index_from_node(n)
            legacy = f"enrich_{slot}"
            if legacy in done:
                return True
            if node_key in completed_node_keys(project):
                return True
            return False
        return typ in done

    def next_running_after_ready(
        self,
        project: Project,
        ready_status: ProjectStatus,
    ) -> ProjectStatus | None:
        """Следующий running-статус по графу после *_ready."""
        if ready_status not in READY_TO_NODE_TYPE:
            return None
        finished_type = READY_TO_NODE_TYPE[ready_status]
        skipped = self.skipped_keys(project)
        start_keys = self.keys_of_type(finished_type)
        if not start_keys and finished_type.startswith("enrich_"):
            try:
                slot = int(finished_type.removeprefix("enrich_"))
            except ValueError:
                slot = 0
            if 1 <= slot <= 5:
                start_keys = self.excel_gpt_keys_for_slot(slot)
        if not start_keys:
            return None

        visited: set[str] = set()
        queue: deque[str] = deque()
        done = self._work_types_done(project)
        for k in start_keys:
            for nxt in self._out.get(k, []):
                queue.append(nxt)

        while queue:
            key = queue.popleft()
            if key in visited:
                continue
            if key in skipped:
                queue.extend(self._out.get(key, []))
                continue
            visited.add(key)
            typ = self.node_type(key)
            if is_passthrough_node_type(typ):
                queue.extend(self._out.get(key, []))
                continue
            if not is_work_node_type(typ):
                queue.extend(self._out.get(key, []))
                continue
            n = self._by_id.get(key) or {}
            if typ == EXCEL_GPT_NODE_TYPE:
                # excel_gpt в done лежит как enrich_N — typ «excel_gpt» сам
                # в done не попадает; иначе первые слоты перезапускаются, а
                # при кривом slotIndex можно сразу уйти в enriching_3.
                slot = slot_index_from_node(n)
                if (
                    f"enrich_{slot}" in done
                    or key in completed_node_keys(project)
                ):
                    queue.extend(self._out.get(key, []))
                    continue
            elif typ in done:
                queue.extend(self._out.get(key, []))
                continue
            preds = self._effective_predecessors(key, skipped)
            if all(self._is_ready(p, project, skipped) for p in preds):
                spec = (
                    spec_for_node(n)
                    if str(n.get("type") or "") == EXCEL_GPT_NODE_TYPE
                    else spec_for_type(typ)
                )
                if spec:
                    return spec.running_status
            queue.extend(self._out.get(key, []))

        return None

    def skip_disabled_running(
        self,
        project: Project,
        target: ProjectStatus | None,
    ) -> ProjectStatus | None:
        if target is None:
            return None
        disabled = disabled_node_types(project)
        if not disabled:
            return target
        if target not in RUNNING_TO_NODE_TYPE:
            return target
        typ = RUNNING_TO_NODE_TYPE[target]
        if typ not in disabled:
            return target
        skipped = self.skipped_keys(project)
        # BFS от target type nodes
        start_keys = self.keys_of_type(typ)
        visited: set[str] = set()
        queue: deque[str] = deque()
        for k in start_keys:
            for nxt in self._out.get(k, []):
                queue.append(nxt)
        done = self._work_types_done(project)
        done.add(typ)  # treat disabled current as done

        while queue:
            key = queue.popleft()
            if key in visited:
                continue
            if key in skipped:
                queue.extend(self._out.get(key, []))
                continue
            visited.add(key)
            ntyp = self.node_type(key)
            if is_passthrough_node_type(ntyp):
                queue.extend(self._out.get(key, []))
                continue
            if not is_work_node_type(ntyp):
                queue.extend(self._out.get(key, []))
                continue
            if ntyp in disabled:
                queue.extend(self._out.get(key, []))
                continue
            n = self._by_id.get(key) or {}
            if ntyp == EXCEL_GPT_NODE_TYPE:
                slot = slot_index_from_node(n)
                if (
                    f"enrich_{slot}" in done
                    or key in completed_node_keys(project)
                ):
                    queue.extend(self._out.get(key, []))
                    continue
            preds = self._effective_predecessors(key, skipped)
            if all(self.node_type(p) in done for p in preds):
                spec = (
                    spec_for_node(n)
                    if str(n.get("type") or "") == EXCEL_GPT_NODE_TYPE
                    else spec_for_type(ntyp)
                )
                if spec:
                    return spec.running_status
            queue.extend(self._out.get(key, []))

        return None

    def derived_node_states(self, project: Project) -> dict[str, NodeRunStatus]:
        """node_key → NodeRunStatus для UI."""
        skipped = self.skipped_keys(project)
        status = project.status
        active_type: str | None = None
        active_state: NodeRunStatus = NodeRunStatus.pending

        if status in RUNNING_TO_NODE_TYPE:
            active_type = RUNNING_TO_NODE_TYPE[status]
            active_state = NodeRunStatus.running
        elif status in READY_TO_NODE_TYPE:
            active_type = READY_TO_NODE_TYPE[status]
            active_state = NodeRunStatus.done
        elif status is ProjectStatus.published:
            active_type = "publish"
            active_state = NodeRunStatus.done

        done_types = self._work_types_done(project)
        out: dict[str, NodeRunStatus] = {}
        flow = self._flow_work_keys(skipped)

        for nid, n in self._by_id.items():
            typ = str(n.get("type") or "")
            if nid in skipped or typ in disabled_node_types(project):
                out[nid] = NodeRunStatus.skipped
                continue
            if is_work_node_type(typ) and nid not in flow:
                out[nid] = NodeRunStatus.skipped
                continue
            if is_hitl_node_type(typ):
                preds = self._effective_predecessors(nid, skipped)
                if preds and all(self.node_type(p) in done_types for p in preds):
                    if active_type and typ == f"hitl_{active_type.replace('image_prompts', 'images')}":
                        out[nid] = NodeRunStatus.waiting_hitl
                    elif status in READY_TO_NODE_TYPE:
                        out[nid] = NodeRunStatus.waiting_hitl
                    else:
                        out[nid] = NodeRunStatus.done
                else:
                    out[nid] = NodeRunStatus.pending
                continue
            if is_config_node_type(typ):
                if status is ProjectStatus.new:
                    out[nid] = NodeRunStatus.pending
                else:
                    topic_ok = bool((project.topic or "").strip())
                    out[nid] = NodeRunStatus.done if topic_ok else NodeRunStatus.pending
                continue
            if is_work_node_type(typ):
                if typ == EXCEL_GPT_NODE_TYPE:
                    slot = slot_index_from_node(n)
                    running = running_status_for_slot(slot)
                    ready = ready_status_for_slot(slot)
                    meta = project.meta if isinstance(project.meta, dict) else {}
                    slots_done = {
                        int(x)
                        for x in (meta.get("enrich_completed_slots") or [])
                        if str(x).isdigit()
                    }
                    if nid in completed_node_keys(project) or slot in slots_done:
                        out[nid] = NodeRunStatus.done
                    elif status == running:
                        out[nid] = NodeRunStatus.running
                    elif status == ready:
                        out[nid] = NodeRunStatus.done
                    else:
                        from app.telegram.menu import status_order as _ord

                        out[nid] = (
                            NodeRunStatus.done
                            if _ord(status) > _ord(ready)
                            else NodeRunStatus.pending
                        )
                    continue
                if typ == active_type:
                    out[nid] = active_state
                elif typ in done_types:
                    out[nid] = NodeRunStatus.done
                else:
                    out[nid] = NodeRunStatus.pending
                continue
            out[nid] = NodeRunStatus.pending
        return out

    def _linear_prereq_met(self, project: Project, step_code: str) -> bool:
        """Линейный prerequisite шага (когда на канвасе нет входящих связей)."""
        from app.telegram.menu import step_by_code, status_order

        step = step_by_code(step_code)
        if step is None:
            return True
        if step.requires is None:
            return True
        return status_order(project.status) >= status_order(step.requires)

    def is_step_reachable(self, project: Project, step_code: str) -> bool:
        """Можно ли запустить step_code с текущего статуса проекта по графу."""
        from app.orchestrator.node_registry import spec_for_step_code

        spec = spec_for_step_code(step_code)
        if spec is None:
            return True
        target_type = spec.node_type
        skipped = self.skipped_keys(project)
        flow = self._flow_work_keys(skipped)
        target_keys = [
            k
            for k in self.keys_of_type(target_type)
            if k not in skipped and k in flow
        ]
        if not target_keys and step_code == "excel_gpt":
            target_keys = [
                k
                for k in self.keys_of_type(EXCEL_GPT_NODE_TYPE)
                if k not in skipped and k in flow
            ]
        all_keys = list(self.keys_of_type(target_type))
        if not all_keys and step_code == "excel_gpt":
            all_keys = list(self.keys_of_type(EXCEL_GPT_NODE_TYPE))
        if not target_keys:
            if all_keys:
                return False
            return self._linear_prereq_met(project, step_code)
        status = project.status
        if status in RUNNING_TO_NODE_TYPE and RUNNING_TO_NODE_TYPE[status] == target_type:
            return True
        if status in READY_TO_NODE_TYPE and READY_TO_NODE_TYPE[status] == target_type:
            return True
        if step_code == "excel_gpt" and status in RUNNING_TO_NODE_TYPE:
            slot = slot_from_running_status(status)
            if slot is not None and self.excel_gpt_keys_for_slot(slot):
                return True
        for key in target_keys:
            preds = self._effective_predecessors(key, skipped)
            if not preds:
                if target_type == "plan":
                    return True
                return self._linear_prereq_met(project, step_code)
            if all(self._is_ready(p, project, skipped) for p in preds):
                return True
        return False

async def load_graph_for_project(
    session: AsyncSession,
    project: Project,
) -> WorkflowGraph:
    from app.services.canvas_graph import canvas_graph_from_meta

    meta = project.meta if isinstance(project.meta, dict) else {}
    cg = canvas_graph_from_meta(meta)
    if cg:
        return WorkflowGraph(list(cg["nodes"]), list(cg["edges"]))

    run = (
        await session.execute(
            select(WorkflowRun).where(WorkflowRun.project_id == project.id)
        )
    ).scalar_one_or_none()
    if run is not None:
        nodes = list(run.nodes_snapshot or [])
        edges = list(run.edges_snapshot or [])
        if nodes:
            return WorkflowGraph(nodes, edges)
        if run.workflow_id:
            from app.models import Workflow

            wf = await session.get(Workflow, run.workflow_id)
            if wf and wf.nodes:
                return WorkflowGraph(list(wf.nodes or []), list(wf.edges or []))
    return WorkflowGraph.default()


async def assert_step_allowed_by_graph(
    session: AsyncSession,
    project: Project,
    step_code: str,
) -> None:
    """Ручной запуск ноды всегда разрешён.

    Порядок графа / ``is_step_reachable`` используется только для
    auto_advance и подсказок UI. Пользователь в Studio/TG может запустить
    любую ноду в любой момент (если уже не крутится другой running-шаг).
    """
    _ = (session, project, step_code)
    return


def sync_skip_disabled(
    project: Project,
    target: ProjectStatus | None,
    graph: WorkflowGraph | None = None,
) -> ProjectStatus | None:
    g = graph or WorkflowGraph.default()
    return g.skip_disabled_running(project, target)


def sync_next_after_ready(
    project: Project,
    ready_status: ProjectStatus,
    graph: WorkflowGraph | None = None,
) -> ProjectStatus | None:
    g = graph or WorkflowGraph.default()
    return g.next_running_after_ready(project, ready_status)
