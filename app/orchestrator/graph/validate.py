"""Валидация workflow-графа перед сохранением."""

from __future__ import annotations

from typing import Any

from app.orchestrator.node_registry import is_work_node_type

# Ветка «не ок» — допустимый обратный ход (проверка → починить → снова проверка).
_FEEDBACK_EDGE_KINDS = frozenset({"fail"})


def _edge_kind(edge: dict[str, Any]) -> str:
    data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
    kind = str((data or {}).get("kind") or edge.get("kind") or "after").strip().lower()
    if kind in ("ok", "если ok", "если_ok"):
        return "pass"
    if kind in ("не ok", "не ок", "not_ok"):
        return "fail"
    return kind


def validate_workflow_graph(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    by_id: dict[str, dict[str, Any]] = {}
    for n in nodes or []:
        nid = n.get("id")
        if not nid:
            errors.append("нода без id")
            continue
        sid = str(nid)
        if sid in by_id:
            errors.append(f"дублирующийся id ноды: {sid}")
        by_id[sid] = n

    out: dict[str, list[str]] = {nid: [] for nid in by_id}
    # Граф без feedback-стрелок — для проверки «жёстких» циклов.
    out_forward: dict[str, list[str]] = {nid: [] for nid in by_id}
    rev: dict[str, list[str]] = {nid: [] for nid in by_id}
    feedback_count = 0

    for e in edges or []:
        src, tgt = str(e.get("source") or ""), str(e.get("target") or "")
        if not src or not tgt:
            errors.append("связь без source или target")
            continue
        if src not in by_id:
            errors.append(f"связь из несуществующей ноды: {src}")
            continue
        if tgt not in by_id:
            errors.append(f"связь в несуществующую ноду: {tgt}")
            continue
        out[src].append(tgt)
        rev[tgt].append(src)
        kind = _edge_kind(e)
        if kind in _FEEDBACK_EDGE_KINDS:
            feedback_count += 1
            continue
        out_forward[src].append(tgt)

    cycle = _find_cycle(out_forward)
    if cycle:
        errors.append(f"цикл в графе: {' → '.join(cycle)}")
    elif feedback_count:
        # Полный граф с fail может замыкаться — это ок для ok/не ок.
        soft = _find_cycle(out)
        if soft:
            warnings.append(
                "есть петля через «Не ок» (проверка → правка → снова) — так и задумано"
            )

    work_nodes = [
        nid for nid, n in by_id.items() if is_work_node_type(str(n.get("type") or ""))
    ]
    if not work_nodes:
        warnings.append("нет рабочих нод (plan, script, …)")
    else:
        entry = [nid for nid in work_nodes if not rev.get(nid)]
        if not entry:
            warnings.append("нет входной рабочей ноды — все имеют предшественников")

    isolated = [
        nid for nid in by_id if not out.get(nid) and not rev.get(nid)
    ]
    if isolated:
        warnings.append(f"изолированные ноды ({len(isolated)}): {', '.join(isolated[:5])}")

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


def _find_cycle(out: dict[str, list[str]]) -> list[str] | None:
    visited: set[str] = set()
    stack: set[str] = set()
    path: list[str] = []

    def dfs(u: str) -> list[str] | None:
        visited.add(u)
        stack.add(u)
        path.append(u)
        for v in out.get(u, []):
            if v not in visited:
                found = dfs(v)
                if found:
                    return found
            elif v in stack:
                i = path.index(v)
                return path[i:] + [v]
        stack.remove(u)
        path.pop()
        return None

    for node in out:
        if node not in visited:
            found = dfs(node)
            if found:
                return found
    return None
