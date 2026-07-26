"""Валидация workflow-графа перед сохранением."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.orchestrator.node_registry import is_work_node_type

# Ветка «не ок» — допустимый обратный ход (проверка → починить → снова проверка).
_FEEDBACK_EDGE_KINDS = frozenset({"fail"})
# Только «связь» можно автопометить как fail. pass/gate («Ок») не трогаем.
_AUTO_FAILABLE_KINDS = frozenset({"after", "feed", "review", ""})


def _edge_kind(edge: dict[str, Any]) -> str:
    data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
    kind = str((data or {}).get("kind") or edge.get("kind") or "after").strip().lower()
    if kind in ("ok", "если ok", "если_ok"):
        return "pass"
    if kind in ("не ok", "не ок", "not_ok"):
        return "fail"
    return kind


def _set_edge_kind(edge: dict[str, Any], kind: str) -> None:
    data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
    data = dict(data or {})
    data["kind"] = kind
    edge["data"] = data
    if "kind" in edge:
        edge["kind"] = kind


def _node_type(node: dict[str, Any] | None) -> str:
    if not node:
        return ""
    return str(node.get("type") or "").strip().lower()


def _is_check_node_type(typ: str) -> bool:
    """Ноды-проверки: с них петля «на переделку» — нормальная схема."""
    t = (typ or "").strip().lower()
    return t == "excel_gpt" or t.startswith("enrich_")


def normalize_check_feedback_edges(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, str]], set[str]]:
    """Смягчить петли через excel_gpt.

    - after с проверки, замыкающий цикл → fail («Не ок»)
    - pass/gate («Ок») никогда не переписываем; для validate исключаем из жёсткого графа

    Returns: edges, warnings, patches[{id,kind}], soft_exclude_ids
    """
    by_id: dict[str, dict[str, Any]] = {}
    for n in nodes or []:
        nid = n.get("id")
        if nid:
            by_id[str(nid)] = n

    out_edges = deepcopy(list(edges or []))
    warnings: list[str] = []
    patches: list[dict[str, str]] = []
    soft_exclude: set[str] = set()
    soft_pass_warned = False

    for _ in range(max(8, len(out_edges) + 1)):
        out_forward: dict[str, list[str]] = {nid: [] for nid in by_id}
        edge_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for e in out_edges:
            eid = str(e.get("id") or "")
            src, tgt = str(e.get("source") or ""), str(e.get("target") or "")
            if src not in by_id or tgt not in by_id:
                continue
            kind = _edge_kind(e)
            edge_by_pair.setdefault((src, tgt), []).append(e)
            if kind in _FEEDBACK_EDGE_KINDS or eid in soft_exclude:
                continue
            out_forward[src].append(tgt)

        cycle = _find_cycle(out_forward)
        if not cycle:
            break

        # 1) «Связь» с check-ноды → «Не ок»
        marked = False
        for i in range(len(cycle) - 1):
            u, v = cycle[i], cycle[i + 1]
            if not _is_check_node_type(_node_type(by_id.get(u))):
                continue
            for e in edge_by_pair.get((u, v), []):
                if _edge_kind(e) not in _AUTO_FAILABLE_KINDS:
                    continue
                eid = str(e.get("id") or "")
                _set_edge_kind(e, "fail")
                if not e.get("label") or str(e.get("label")).lower() in (
                    "связь",
                    "after",
                    "→",
                ):
                    e["label"] = "не ок"
                if eid:
                    patches.append({"id": eid, "kind": "fail"})
                marked = True
            if marked:
                break
        if marked:
            continue

        # 2) Цикл через «Ок» — kind не трогаем, только исключаем из жёсткой проверки
        softened = False
        for i in range(len(cycle) - 1):
            u, v = cycle[i], cycle[i + 1]
            if not _is_check_node_type(_node_type(by_id.get(u))):
                continue
            for e in edge_by_pair.get((u, v), []):
                eid = str(e.get("id") or "")
                if _edge_kind(e) in ("pass", "gate") and eid and eid not in soft_exclude:
                    soft_exclude.add(eid)
                    softened = True
            if softened:
                break
        if softened:
            if not soft_pass_warned:
                warnings.append(
                    "петля через ветку «Ок» у проверяющей ноды — kind не меняли"
                )
                soft_pass_warned = True
            continue

        break

    if patches:
        n = len(patches)
        warnings.append(
            f"петля через проверку: {n} стрелк"
            f"{'а' if n == 1 else 'и'} «Связь»→«Не ок» (ветки «Ок» не трогали)"
        )
    return out_edges, warnings, patches, soft_exclude


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

    norm_edges, norm_warnings, patches, soft_exclude = normalize_check_feedback_edges(
        nodes, edges
    )
    warnings.extend(norm_warnings)

    out: dict[str, list[str]] = {nid: [] for nid in by_id}
    out_forward: dict[str, list[str]] = {nid: [] for nid in by_id}
    rev: dict[str, list[str]] = {nid: [] for nid in by_id}
    feedback_count = 0

    for e in norm_edges:
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
        eid = str(e.get("id") or "")
        if kind in _FEEDBACK_EDGE_KINDS or eid in soft_exclude:
            feedback_count += 1
            continue
        out_forward[src].append(tgt)

    cycle = _find_cycle(out_forward)
    if cycle:
        errors.append(f"цикл в графе: {' → '.join(cycle)}")
    elif feedback_count:
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

    result: dict[str, Any] = {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "edge_patches": patches,
    }
    # Полный список — только если реально меняли kind (для save на бэке).
    if patches:
        result["edges"] = norm_edges
    return result


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
