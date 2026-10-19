from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.excel_gpt_node import (
    EXCEL_GPT_NODE_TYPE,
    MAX_EXCEL_GPT_SLOTS,
    default_excel_gpt_label,
    legacy_enrich_slot_from_type,
    sd_agent_marker,
    slot_index_from_node,
)

ENRICH_TO_EXCEL_GPT_ID = "graph_0001_enrich_to_excel_gpt"
GRAPH_MIGRATIONS_META_KEY = "graph_migrations"
_MAX_RECORDS = 5


def is_legacy_enrich_node(node: dict[str, Any]) -> bool:
    return isinstance(node, dict) and legacy_enrich_slot_from_type(str(node.get("type") or "")) is not None


@dataclass
class NodeChange:
    node_id: str
    from_type: str
    to_type: str
    slot_before: int
    slot_after: int
    slot_index_added: bool
    label_changed: bool = False


@dataclass
class GraphMigrationResult:
    nodes: list[dict[str, Any]]
    changes: list[NodeChange] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    rollback: list[dict[str, Any]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    def report(self) -> dict[str, Any]:
        return {"migration": ENRICH_TO_EXCEL_GPT_ID, "changed": self.changed,
                "changes": [asdict(c) for c in self.changes], "skipped": list(self.skipped)}


def migrate_enrich_graph(nodes: list[dict[str, Any]] | None) -> GraphMigrationResult:
    out: list[dict[str, Any]] = []
    result = GraphMigrationResult(nodes=out)
    for node in nodes or []:
        if not is_legacy_enrich_node(node):
            out.append(node)
            continue
        nid = str(node.get("id") or "")
        old_type = str(node.get("type") or "")
        legacy_slot = legacy_enrich_slot_from_type(old_type)
        data = copy.deepcopy(node.get("data")) if isinstance(node.get("data"), dict) else {}
        explicit = data.get("slotIndex")
        has_explicit_slot = isinstance(explicit, int) and not isinstance(explicit, bool) \
            and 1 <= explicit <= MAX_EXCEL_GPT_SLOTS
        added = False
        if not has_explicit_slot and not data.get("slotOverflow") and not sd_agent_marker(node):
            data["slotIndex"] = legacy_slot
            added = True
        label = str(data.get("label") or "").strip()
        label_changed = False
        if not label:
            data["label"] = default_excel_gpt_label(legacy_slot or 1)
            label_changed = True
        new_node = {**copy.deepcopy(node), "type": EXCEL_GPT_NODE_TYPE, "data": data}
        before, after = slot_index_from_node(node), slot_index_from_node(new_node)
        if before != after:
            result.skipped.append({"node_id": nid, "reason": f"slot would change {before} -> {after}"})
            out.append(node)
            continue
        out.append(new_node)
        result.rollback.append(copy.deepcopy(node))
        result.changes.append(NodeChange(nid, old_type, EXCEL_GPT_NODE_TYPE, before, after, added, label_changed))
    return result


def rollback_enrich_graph(nodes: list[dict[str, Any]] | None, rollback: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
    originals = {str(n.get("id")): n for n in rollback or [] if isinstance(n, dict) and n.get("id")}
    restored: list[str] = []
    out: list[dict[str, Any]] = []
    for node in nodes or []:
        nid = str((node or {}).get("id") or "")
        if nid in originals and str(node.get("type") or "") == EXCEL_GPT_NODE_TYPE:
            out.append(copy.deepcopy(originals[nid]))
            restored.append(nid)
        else:
            out.append(node)
    return out, restored


def _record(result: GraphMigrationResult, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    rec = {"id": ENRICH_TO_EXCEL_GPT_ID, "applied_at": datetime.now(timezone.utc).isoformat(),
           "node_ids": [c.node_id for c in result.changes], "before": result.rollback}
    if extra:
        rec.update(extra)
    return rec


def _with_record(meta: Any, record: dict[str, Any]) -> dict[str, Any]:
    meta = dict(meta) if isinstance(meta, dict) else {}
    records = [r for r in meta.get(GRAPH_MIGRATIONS_META_KEY) or [] if isinstance(r, dict)]
    meta[GRAPH_MIGRATIONS_META_KEY] = (records + [record])[-_MAX_RECORDS:]
    return meta


def _pop_record(meta: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
    meta = dict(meta) if isinstance(meta, dict) else {}
    records = [r for r in meta.get(GRAPH_MIGRATIONS_META_KEY) or [] if isinstance(r, dict)]
    mine = [r for r in records if r.get("id") == ENRICH_TO_EXCEL_GPT_ID]
    if not mine:
        return meta, None
    rest = [r for r in records if r is not mine[-1]]
    if rest:
        meta[GRAPH_MIGRATIONS_META_KEY] = rest
    else:
        meta.pop(GRAPH_MIGRATIONS_META_KEY, None)
    return meta, mine[-1]


def _store_report(kind: str, ident: Any, result: GraphMigrationResult, **extra: Any) -> dict[str, Any]:
    return {"store": kind, "id": ident, **result.report(), **extra}


async def migrate_stores(session: AsyncSession, *, dry_run: bool = True) -> dict[str, Any]:
    from app.models import NodeRun, Project, Workflow, WorkflowRun

    items: list[dict[str, Any]] = []
    for wf in (await session.execute(select(Workflow))).scalars().all():
        res = migrate_enrich_graph(list(wf.nodes or []))
        if not (res.changed or res.skipped):
            continue
        items.append(_store_report("workflow", wf.id, res))
        if res.changed and not dry_run:
            wf.nodes = res.nodes
            wf.version = (wf.version or 1) + 1
            wf.meta = _with_record(wf.meta, _record(res))
    for run in (await session.execute(select(WorkflowRun))).scalars().all():
        res = migrate_enrich_graph(list(run.nodes_snapshot or []))
        if not (res.changed or res.skipped):
            continue
        changed_ids = {c.node_id for c in res.changes}
        node_runs = (await session.execute(select(NodeRun).where(
            NodeRun.workflow_run_id == run.id, NodeRun.node_key.in_(changed_ids)))).scalars().all() \
            if changed_ids else []
        old_types = {nr.node_key: nr.node_type for nr in node_runs}
        items.append(_store_report("workflow_run", run.id, res, node_runs=sorted(old_types)))
        if res.changed and not dry_run:
            run.nodes_snapshot = res.nodes
            run.meta = _with_record(run.meta, _record(res, {"node_run_types": old_types}))
            for nr in node_runs:
                nr.node_type = EXCEL_GPT_NODE_TYPE
    for project in (await session.execute(select(Project))).scalars().all():
        meta = project.meta if isinstance(project.meta, dict) else {}
        graph = meta.get("canvas_graph")
        if not isinstance(graph, dict) or not isinstance(graph.get("nodes"), list):
            continue
        res = migrate_enrich_graph(graph["nodes"])
        if not (res.changed or res.skipped):
            continue
        items.append(_store_report("project_canvas", project.id, res))
        if res.changed and not dry_run:
            new_meta = _with_record(meta, _record(res))
            new_meta["canvas_graph"] = {**graph, "nodes": res.nodes}
            project.meta = new_meta
    if not dry_run:
        await session.flush()
    return {"migration": ENRICH_TO_EXCEL_GPT_ID, "dry_run": dry_run,
            "stores_changed": sum(1 for i in items if i["changed"]),
            "nodes_changed": sum(len(i["changes"]) for i in items), "items": items}


async def rollback_stores(session: AsyncSession) -> dict[str, Any]:
    from app.models import NodeRun, Project, Workflow, WorkflowRun

    restored: list[dict[str, Any]] = []
    for wf in (await session.execute(select(Workflow))).scalars().all():
        meta, rec = _pop_record(wf.meta)
        if rec is None:
            continue
        nodes, ids = rollback_enrich_graph(list(wf.nodes or []), rec.get("before") or [])
        wf.nodes, wf.meta, wf.version = nodes, meta, (wf.version or 1) + 1
        restored.append({"store": "workflow", "id": wf.id, "restored": ids})
    for run in (await session.execute(select(WorkflowRun))).scalars().all():
        meta, rec = _pop_record(run.meta)
        if rec is None:
            continue
        nodes, ids = rollback_enrich_graph(list(run.nodes_snapshot or []), rec.get("before") or [])
        run.nodes_snapshot, run.meta = nodes, meta
        old_types = rec.get("node_run_types") or {}
        if old_types:
            for nr in (await session.execute(select(NodeRun).where(
                    NodeRun.workflow_run_id == run.id, NodeRun.node_key.in_(list(old_types))))).scalars().all():
                nr.node_type = old_types[nr.node_key]
        restored.append({"store": "workflow_run", "id": run.id, "restored": ids})
    for project in (await session.execute(select(Project))).scalars().all():
        meta = project.meta if isinstance(project.meta, dict) else {}
        graph = meta.get("canvas_graph")
        new_meta, rec = _pop_record(meta)
        if rec is None or not isinstance(graph, dict):
            continue
        nodes, ids = rollback_enrich_graph(list(graph.get("nodes") or []), rec.get("before") or [])
        new_meta["canvas_graph"] = {**graph, "nodes": nodes}
        project.meta = new_meta
        restored.append({"store": "project_canvas", "id": project.id, "restored": ids})
    await session.flush()
    return {"migration": ENRICH_TO_EXCEL_GPT_ID, "restored": restored}



class MigrationError(RuntimeError):
    pass


def backup_database(db_path: Path, backup_dir: Path | None = None) -> Path:
    import sqlite3

    db_path = Path(db_path)
    if not db_path.is_file():
        raise MigrationError(f"файл базы не найден: {db_path}")
    backup_dir = Path(backup_dir) if backup_dir else db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = backup_dir / f"{db_path.stem}.pre_graph_migration.{stamp}{db_path.suffix}"
    n = 1
    while dest.exists():
        dest = backup_dir / f"{db_path.stem}.pre_graph_migration.{stamp}_{n}{db_path.suffix}"
        n += 1
    src = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=60)
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
            ok = dst.execute("PRAGMA integrity_check").fetchone()
        finally:
            dst.close()
    finally:
        src.close()
    if not ok or ok[0] != "ok":
        dest.unlink(missing_ok=True)
        raise MigrationError(f"резервная копия не прошла проверку целостности: {ok}")
    return dest


def format_report_ru(report: dict[str, Any]) -> str:
    dry = bool(report.get("dry_run", True))
    head = "СУХОЙ ПРОХОД (ничего не записано)" if dry else "ПРИМЕНЕНО"
    verb = "изменится" if dry else "изменено"
    lines = [f"{head}: миграция узлов enrich_1..5 -> excel_gpt",
             f"Хранилищ, где {verb} графы: {report.get('stores_changed', 0)}; узлов {verb}: {report.get('nodes_changed', 0)}"]
    names = {"workflow": "шаблон графа", "workflow_run": "запуск", "project_canvas": "холст проекта"}
    for item in report.get("items", []):
        lines.append(f"- {names.get(item['store'], item['store'])} #{item['id']}")
        for c in item.get("changes", []):
            extra = []
            if c.get("slot_index_added"):
                extra.append("слот записан в data.slotIndex")
            if c.get("label_changed"):
                extra.append("пустая подпись заменена на стандартную")
            tail = f" ({'; '.join(extra)})" if extra else ""
            lines.append(f"    узел {c['node_id']}: {c['from_type']} -> {c['to_type']}, слот {c['slot_before']}{tail}")
        for s in item.get("skipped", []):
            lines.append(f"    ПРОПУЩЕН узел {s['node_id']}: {s['reason']}")
    if not report.get("items"):
        lines.append("Узлов enrich_N не найдено: менять нечего.")
    return "\n".join(lines)


async def count_enrich_nodes(session: AsyncSession) -> int:
    from app.models import Project, Workflow, WorkflowRun

    total = 0
    for wf in (await session.execute(select(Workflow))).scalars().all():
        total += sum(1 for n in wf.nodes or [] if is_legacy_enrich_node(n))
    for run in (await session.execute(select(WorkflowRun))).scalars().all():
        total += sum(1 for n in run.nodes_snapshot or [] if is_legacy_enrich_node(n))
    for project in (await session.execute(select(Project))).scalars().all():
        graph = (project.meta or {}).get("canvas_graph") if isinstance(project.meta, dict) else None
        if isinstance(graph, dict) and isinstance(graph.get("nodes"), list):
            total += sum(1 for n in graph["nodes"] if is_legacy_enrich_node(n))
    return total


async def run_migration(session_factory: Any, db_path: Path, *, apply: bool = False, rollback: bool = False,
                        backup_dir: Path | None = None) -> dict[str, Any]:
    if apply and rollback:
        raise MigrationError("--apply и --rollback нельзя вместе")
    if rollback:
        backup = backup_database(db_path, backup_dir)
        async with session_factory() as session:
            out = await rollback_stores(session)
            await session.commit()
        async with session_factory() as session:
            out["enrich_nodes_after"] = await count_enrich_nodes(session)
        out.update(mode="rollback", backup=str(backup))
        return out
    if not apply:
        async with session_factory() as session:
            out = await migrate_stores(session, dry_run=True)
            out["enrich_nodes_total"] = await count_enrich_nodes(session)
        out["mode"] = "dry_run"
        return out
    backup = backup_database(db_path, backup_dir)
    async with session_factory() as session:
        try:
            out = await migrate_stores(session, dry_run=False)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            raise MigrationError(f"запись не удалась, база не изменена; копия: {backup}; причина: {exc}") from exc
    async with session_factory() as session:
        again = await migrate_stores(session, dry_run=True)
        left = await count_enrich_nodes(session)
    verified = again["nodes_changed"] == 0
    out.update(mode="apply", backup=str(backup), verified=verified, enrich_nodes_after=left)
    if not verified:
        async with session_factory() as session:
            await rollback_stores(session)
            await session.commit()
        out["auto_rolled_back"] = True
    return out
