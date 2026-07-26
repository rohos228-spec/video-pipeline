"""Оператор GPT: контракт, сверка файлов со стрелками, без браузера.

Источник правды при запуске:
  meta.excel_gpt_nodes[nodeKey] + meta.canvas_graph.edges + файлы на диске.

UI и node.data — только кэш отрисовки; resolve всегда перепроверяет диск.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from app.models import Project
from app.services.canvas_graph import canvas_graph_from_meta
from app.services.excel_gpt_node import (
    EXCEL_GPT_NODE_TYPE,
    attachment_paths,
    is_excel_gpt_node_type,
    node_config,
    upload_dir,
)

EdgeKind = Literal["after", "feed", "review", "gate", "pass", "fail"]
OperatorRole = Literal["assist", "review", "transform", "extract", "compare", "gate"]
OutputMode = Literal["text", "project_file", "sidecar"]
InputOrigin = Literal["upload", "edge", "project", "snapshot"]

# gate — legacy «если ok»; pass/fail — явные ветки вердикта.
VALID_EDGE_KINDS: frozenset[str] = frozenset(
    {"after", "feed", "review", "gate", "pass", "fail"}
)
VALID_ROLES: frozenset[str] = frozenset(
    {"assist", "review", "transform", "extract", "compare", "gate"}
)
VALID_OUTPUTS: frozenset[str] = frozenset({"text", "project_file", "sidecar"})

# Роли с вердиктом ок/не ок → две исходящие ветки.
BRANCHING_ROLES: frozenset[str] = frozenset({"review", "gate", "compare"})

ROLE_DEFAULT_LABELS: dict[str, str] = {
    "assist": "Работа с GPT",
    "review": "Ок / не ок",
    "transform": "Переделывает",
    "extract": "Достаёт данные",
    "compare": "Сравнивает",
    "gate": "Ок / не ок",
}
_DEFAULT_LABEL_SET: frozenset[str] = frozenset(
    {*ROLE_DEFAULT_LABELS.values(), "Работа с GPT", ""}
)

_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
_VIDEO_SUFFIXES = frozenset({".mp4", ".webm", ".mov", ".mkv"})
_DOC_SUFFIXES = frozenset({".xlsx", ".xls", ".txt", ".md", ".json", ".csv", ".pdf"})
_ANY_SUFFIXES = _IMAGE_SUFFIXES | _VIDEO_SUFFIXES | _DOC_SUFFIXES


def normalize_edge_kind(raw: Any) -> EdgeKind:
    s = str(raw or "after").strip().lower()
    # синонимы UI / legacy
    if s in ("ok", "если ok", "если_ok", "pass_ok"):
        s = "pass"
    if s in ("не ok", "не ок", "neok", "not_ok", "reject"):
        s = "fail"
    if s in VALID_EDGE_KINDS:
        return s  # type: ignore[return-value]
    return "after"


def is_pass_edge_kind(kind: str) -> bool:
    """True для ветки «ок» (включая legacy gate)."""
    return kind in ("pass", "gate")


def is_fail_edge_kind(kind: str) -> bool:
    return kind == "fail"


def is_verdict_edge_kind(kind: str) -> bool:
    return is_pass_edge_kind(kind) or is_fail_edge_kind(kind)


def default_label_for_role(role: OperatorRole | str) -> str:
    return ROLE_DEFAULT_LABELS.get(str(role), "Работа с GPT")


def edge_kind_of(edge: dict[str, Any]) -> EdgeKind:
    data = edge.get("data") if isinstance(edge.get("data"), dict) else {}
    return normalize_edge_kind(data.get("kind") or edge.get("kind"))


def normalize_role(raw: Any, *, fallback: str = "assist") -> OperatorRole:
    s = str(raw or fallback).strip().lower()
    # legacy workMode → role
    if s == "assist":
        return "assist"
    if s in VALID_ROLES:
        return s  # type: ignore[return-value]
    return "assist"  # type: ignore[return-value]


def normalize_output_mode(raw: Any, *, role: OperatorRole) -> OutputMode:
    s = str(raw or "").strip().lower()
    if s in VALID_OUTPUTS:
        return s  # type: ignore[return-value]
    if role in ("review", "extract", "compare", "gate"):
        return "text"
    if role == "transform":
        return "sidecar"
    return "project_file"


def operator_config(project: Project, node_key: str) -> dict[str, Any]:
    cfg = dict(node_config(project, node_key))
    role = normalize_role(cfg.get("role") or cfg.get("workMode") or "assist")
    cfg["role"] = role
    cfg["workMode"] = role if role in ("assist", "review", "transform") else "assist"
    cfg["outputMode"] = normalize_output_mode(cfg.get("outputMode"), role=role)
    cfg["useSnapshot"] = bool(cfg.get("useSnapshot"))
    raw_transport = str(cfg.get("transport") or "").strip().lower()
    if raw_transport in ("api", "browser"):
        cfg["transport"] = raw_transport
    else:
        # По умолчанию API для новой механики; legacy assist без ролей — browser.
        has_operator_fields = any(
            k in cfg
            for k in ("role", "outputMode", "useSnapshot", "uploadedFileNames")
        )
        cfg["transport"] = "api" if has_operator_fields else "browser"
    # multi-file uploads stored as list of names
    names = cfg.get("uploadedFileNames")
    if not isinstance(names, list):
        single = str(cfg.get("uploadedFileName") or "").strip()
        names = [single] if single else []
    cfg["uploadedFileNames"] = [str(x).strip() for x in names if str(x).strip()]
    return cfg


def _file_probe(path: Path, *, origin: InputOrigin, from_node: str | None = None) -> dict[str, Any]:
    exists = path.is_file()
    size = int(path.stat().st_size) if exists else 0
    ok = exists and size > 0
    suffix = path.suffix.lower()
    kind = "other"
    if suffix in _IMAGE_SUFFIXES:
        kind = "image"
    elif suffix in _VIDEO_SUFFIXES:
        kind = "video"
    elif suffix in {".xlsx", ".xls"}:
        kind = "xlsx"
    elif suffix in {".txt", ".md", ".csv", ".json"}:
        kind = "text"
    preview = f"/api/files?path={path}" if ok and kind in ("image", "video", "text") else None
    return {
        "name": path.name,
        "path": str(path),
        "exists": exists,
        "size": size,
        "ok": ok,
        "kind": kind,
        "origin": origin,
        "fromNode": from_node,
        "preview_url": preview,
        "error": None if ok else ("файл отсутствует" if not exists else "файл пустой"),
    }


def _collect_under(root: Path, *, suffixes: frozenset[str], limit: int = 12) -> list[Path]:
    if not root.is_dir():
        return []
    out: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in suffixes:
            continue
        if p.stat().st_size < 1:
            continue
        out.append(p)
        if len(out) >= limit:
            break
    return out


def _node_type_map(project: Project) -> dict[str, str]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    cg = canvas_graph_from_meta(meta) or {}
    out: dict[str, str] = {}
    for n in cg.get("nodes") or []:
        if not isinstance(n, dict):
            continue
        nid = str(n.get("id") or "").strip()
        if nid:
            out[nid] = str(n.get("type") or "")
    return out


def _incoming_edges(project: Project, node_key: str) -> list[dict[str, Any]]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    cg = canvas_graph_from_meta(meta) or {}
    edges = cg.get("edges") or []
    result: list[dict[str, Any]] = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        if str(e.get("target") or "") != node_key:
            continue
        result.append(e)
    return result


def _outgoing_edges(project: Project, node_key: str) -> list[dict[str, Any]]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    cg = canvas_graph_from_meta(meta) or {}
    edges = cg.get("edges") or []
    result: list[dict[str, Any]] = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        if str(e.get("source") or "") != node_key:
            continue
        result.append(e)
    return result


def _snapshot_xlsx_for_node(project: Project, source_key: str) -> Path | None:
    meta = project.meta if isinstance(project.meta, dict) else {}
    snaps = meta.get("xlsx_snapshots_by_node")
    if not isinstance(snaps, dict):
        return None
    entry = snaps.get(source_key)
    if not isinstance(entry, dict):
        return None
    name = str(entry.get("name") or "").strip()
    rel = str(entry.get("path") or "").strip()
    candidates: list[Path] = []
    if rel:
        candidates.append(Path(rel))
        candidates.append(project.data_dir / rel)
    if name:
        candidates.append(project.data_dir / "old" / name)
        candidates.append(project.data_dir / name)
    for p in candidates:
        if p.is_file() and p.stat().st_size > 0:
            return p
    return None


def files_from_source_node(
    project: Project,
    source_key: str,
    *,
    use_snapshot: bool = False,
    limit: int = 12,
) -> list[Path]:
    """Фактические файлы результата ноды-источника."""
    types = _node_type_map(project)
    typ = types.get(source_key, "")
    root = project.data_dir
    found: list[Path] = []

    if use_snapshot:
        snap = _snapshot_xlsx_for_node(project, source_key)
        if snap is not None:
            return [snap]

    # Явный результат оператора / excel_gpt
    meta = project.meta if isinstance(project.meta, dict) else {}
    results = meta.get("gpt_operator_results")
    if isinstance(results, dict):
        entry = results.get(source_key)
        if isinstance(entry, dict):
            for key in ("outputPaths", "inputPaths"):
                raw = entry.get(key) or []
                if isinstance(raw, list):
                    for item in raw:
                        p = Path(str(item))
                        if p.is_file():
                            found.append(p)
            if found:
                return found[:limit]

    if typ in ("images", "hitl_images") or typ == "image_prompts":
        found = _collect_under(root / "scenes", suffixes=_IMAGE_SUFFIXES, limit=limit)
        if not found:
            found = _collect_under(root / "images", suffixes=_IMAGE_SUFFIXES, limit=limit)
        return found

    if typ in ("videos", "hitl_videos", "animation_prompts"):
        return _collect_under(root / "videos", suffixes=_VIDEO_SUFFIXES, limit=limit)

    if typ == "hero":
        return _collect_under(root / "characters", suffixes=_IMAGE_SUFFIXES, limit=limit)

    if typ in ("audio", "music"):
        voice = root / "voiceover.txt"
        if voice.is_file():
            return [voice]
        return _collect_under(root / "audio", suffixes=frozenset({".mp3", ".wav", ".m4a", ".txt"}), limit=limit)

    if is_excel_gpt_node_type(typ) or typ in ("plan", "script", "split", "items", "excel_feed"):
        if use_snapshot:
            snap = _snapshot_xlsx_for_node(project, source_key)
            if snap is not None:
                return [snap]
        xlsx = root / "project.xlsx"
        if xlsx.is_file():
            return [xlsx]

    # fallback: uploads этой ноды
    udir = upload_dir(project, source_key)
    if udir.is_dir():
        for p in sorted(udir.iterdir()):
            if p.is_file() and p.suffix.lower() in _ANY_SUFFIXES and p.name != "gpt_reply.txt":
                found.append(p)
                if len(found) >= limit:
                    break
    return found


def _manual_upload_files(project: Project, node_key: str, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    names = list(cfg.get("uploadedFileNames") or [])
    for name in names:
        p = upload_dir(project, node_key) / Path(name).name
        files.append(_file_probe(p, origin="upload"))
    # legacy single attachment_paths for non-edge sources when no uploads listed
    if not files:
        for p in attachment_paths(project, node_key):
            # skip if it's only project.xlsx and role expects edge — still include as project
            origin: InputOrigin = "upload" if "excel_gpt_uploads" in str(p) else "project"
            files.append(_file_probe(p, origin=origin))
    return files


def resolve_operator(project: Project, node_key: str) -> dict[str, Any]:
    """Полная сверка: стрелки + слоты + диск. Единый ответ для UI и run."""
    cfg = operator_config(project, node_key)
    role: OperatorRole = cfg["role"]
    output_mode: OutputMode = cfg["outputMode"]
    use_snapshot = bool(cfg.get("useSnapshot"))
    errors: list[str] = []
    warnings: list[str] = []

    incoming = _incoming_edges(project, node_key)
    edge_summaries: list[dict[str, Any]] = []
    edge_files: list[dict[str, Any]] = []

    for e in incoming:
        kind = edge_kind_of(e)
        src = str(e.get("source") or "")
        eid = str(e.get("id") or f"{src}->{node_key}")
        summary: dict[str, Any] = {
            "id": eid,
            "source": src,
            "target": node_key,
            "kind": kind,
            "fileCount": 0,
            "ok": True,
            "errors": [],
        }
        if kind in ("feed", "review"):
            paths = files_from_source_node(
                project, src, use_snapshot=use_snapshot or kind == "review"
            )
            if not paths:
                msg = f"стрелка {kind}: у ноды {src} нет файлов на диске"
                summary["ok"] = False
                summary["errors"].append(msg)
                errors.append(msg)
            for p in paths:
                probe = _file_probe(
                    p,
                    origin="snapshot" if use_snapshot else "edge",
                    from_node=src,
                )
                edge_files.append(probe)
                if not probe["ok"]:
                    summary["ok"] = False
                    summary["errors"].append(probe["error"] or "файл битый")
                    errors.append(f"{src}: {probe['error']}")
            summary["fileCount"] = len(paths)
        elif is_verdict_edge_kind(kind):
            # Входящая ветка ок/не ок: источник — проверяющая нода.
            src_cfg = operator_config(project, src) if src else {}
            src_role = str(src_cfg.get("role") or "")
            if src_role not in BRANCHING_ROLES and not is_excel_gpt_node_type(
                _node_type_map(project).get(src, "")
            ):
                warnings.append(
                    f"стрелка {kind} от {src}: ожидается роль «проверяет» / «шлагбаум»"
                )
        edge_summaries.append(summary)

    manual = _manual_upload_files(project, node_key, cfg)
    # Если есть feed/review — ручной project.xlsx из legacy inputSource не дублируем,
    # оставляем только upload-слоты и edge-файлы.
    has_data_edges = any(edge_kind_of(e) in ("feed", "review") for e in incoming)
    if has_data_edges:
        manual = [f for f in manual if f.get("origin") == "upload"]

    all_files = [*edge_files, *manual]
    # de-dupe by path
    seen: set[str] = set()
    unique_files: list[dict[str, Any]] = []
    for f in all_files:
        key = str(f.get("path") or "")
        if key in seen:
            continue
        seen.add(key)
        unique_files.append(f)

    ok_files = [f for f in unique_files if f.get("ok")]
    for f in unique_files:
        if not f.get("ok"):
            errors.append(f"{f.get('name')}: {f.get('error') or 'файл недоступен'}")
    if role == "compare" and len(ok_files) < 2:
        errors.append("роль «сравнить» требует минимум 2 существующих файла на входе")
    if role == "gate" and not ok_files and not any(
        edge_kind_of(e) in ("feed", "review", "after") for e in incoming
    ):
        warnings.append("шлагбаум без входных файлов — проверка будет только по промту")

    if not ok_files and role in ("assist", "transform", "extract", "review"):
        # soft: assist без файлов — ошибка запуска
        errors.append("нет ни одного существующего файла на входе")

    # исходящие стрелки + ветки ок/не ок
    outgoing = []
    for e in _outgoing_edges(project, node_key):
        outgoing.append(
            {
                "id": str(e.get("id") or ""),
                "source": node_key,
                "target": str(e.get("target") or ""),
                "kind": edge_kind_of(e),
            }
        )

    last = {}
    meta = project.meta if isinstance(project.meta, dict) else {}
    results = meta.get("gpt_operator_results")
    if isinstance(results, dict) and isinstance(results.get(node_key), dict):
        last = dict(results[node_key])

    pass_edges = [e for e in outgoing if is_pass_edge_kind(str(e.get("kind") or ""))]
    fail_edges = [e for e in outgoing if is_fail_edge_kind(str(e.get("kind") or ""))]
    branching_enabled = role in BRANCHING_ROLES
    if branching_enabled:
        if not pass_edges:
            warnings.append(
                "нет исходящей стрелки «Ок» — проведите связь и выберите тип «Ок»"
            )
        if not fail_edges:
            warnings.append(
                "нет исходящей стрелки «Не ок» — проведите связь и выберите тип «Не ок»"
            )

    verdict = str(last.get("gateStatus") or cfg.get("gateStatus") or "").strip().lower()
    if verdict not in ("pass", "fail"):
        verdict = ""

    consistent = len(errors) == 0
    return {
        "nodeKey": node_key,
        "nodeType": EXCEL_GPT_NODE_TYPE,
        "role": role,
        "outputMode": output_mode,
        "useSnapshot": use_snapshot,
        "transport": cfg.get("transport") or "api",
        "label": str(cfg.get("label") or default_label_for_role(role)),
        "files": unique_files,
        "okFileCount": len(ok_files),
        "incomingEdges": edge_summaries,
        "outgoingEdges": outgoing,
        "branching": {
            "enabled": branching_enabled,
            "passEdges": pass_edges,
            "failEdges": fail_edges,
            "hasPass": len(pass_edges) > 0,
            "hasFail": len(fail_edges) > 0,
            "verdict": verdict or None,
        },
        "errors": errors,
        "warnings": warnings,
        "consistent": consistent,
        "canRun": consistent and (len(ok_files) > 0 or role == "gate"),
        "lastResult": last,
        "config": {
            "role": role,
            "outputMode": output_mode,
            "useSnapshot": use_snapshot,
            "transport": cfg.get("transport") or "api",
            "uploadedFileNames": list(cfg.get("uploadedFileNames") or []),
            "workMode": cfg.get("workMode"),
            "inputSource": cfg.get("inputSource"),
            "label": str(cfg.get("label") or default_label_for_role(role)),
            "gateStatus": verdict or None,
        },
    }


def patch_operator_config(project: Project, node_key: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Обновить meta.excel_gpt_nodes[nodeKey] и вернуть свежий resolve."""
    meta = dict(project.meta or {})
    configs = dict(meta.get("excel_gpt_nodes") or {})
    cur = dict(configs.get(node_key) or {})

    role_changed = False
    if "role" in patch:
        role = normalize_role(patch.get("role"))
        role_changed = normalize_role(cur.get("role") or cur.get("workMode") or "assist") != role
        cur["role"] = role
        cur["transport"] = str(cur.get("transport") or "api")
        if cur["transport"] not in ("api", "browser"):
            cur["transport"] = "api"
        if role in ("assist", "review", "transform"):
            cur["workMode"] = role
        elif role in ("extract", "compare", "gate"):
            cur["workMode"] = "review"
    if "workMode" in patch and "role" not in patch:
        role = normalize_role(patch.get("workMode"))
        role_changed = normalize_role(cur.get("role") or cur.get("workMode") or "assist") != role
        cur["role"] = role
        cur["workMode"] = role if role in ("assist", "review", "transform") else "assist"
    if "outputMode" in patch:
        cur["outputMode"] = normalize_output_mode(
            patch.get("outputMode"),
            role=normalize_role(cur.get("role") or "assist"),
        )
    if "useSnapshot" in patch:
        cur["useSnapshot"] = bool(patch.get("useSnapshot"))
    if "transport" in patch:
        t = str(patch.get("transport") or "api").strip().lower()
        cur["transport"] = t if t in ("api", "browser") else "api"
    if "label" in patch and patch["label"] is not None:
        cur["label"] = str(patch["label"])
    elif role_changed:
        # Автоподпись при смене роли, если текст ещё дефолтный / пустой.
        prev_label = str(cur.get("label") or "").strip()
        if prev_label in _DEFAULT_LABEL_SET:
            cur["label"] = default_label_for_role(
                normalize_role(cur.get("role") or "assist")
            )
    if "uploadedFileNames" in patch and isinstance(patch["uploadedFileNames"], list):
        cur["uploadedFileNames"] = [str(x) for x in patch["uploadedFileNames"] if x]
        if cur["uploadedFileNames"]:
            cur["uploadedFileName"] = cur["uploadedFileNames"][0]
            cur["inputSource"] = "upload"
    if "inputSource" in patch:
        cur["inputSource"] = patch["inputSource"]
    if "uploadedFileName" in patch:
        cur["uploadedFileName"] = patch["uploadedFileName"]

    configs[node_key] = cur
    meta["excel_gpt_nodes"] = configs
    meta["active_excel_gpt_node_key"] = node_key
    project.meta = meta
    return resolve_operator(project, node_key)


def set_edge_kind_in_canvas(
    project: Project, edge_id: str, kind: EdgeKind
) -> dict[str, Any] | None:
    """Меняет edge.data.kind в canvas_graph. Возвращает обновлённое ребро или None."""
    meta = dict(project.meta or {})
    cg = canvas_graph_from_meta(meta)
    if not cg:
        return None
    edges = list(cg.get("edges") or [])
    updated = None
    new_edges: list[dict[str, Any]] = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        if str(e.get("id") or "") != edge_id:
            new_edges.append(e)
            continue
        data = dict(e.get("data") or {})
        data["kind"] = normalize_edge_kind(kind)
        ne = {**e, "data": data}
        new_edges.append(ne)
        updated = ne
    if updated is None:
        return None
    raw = dict(meta.get("canvas_graph") or {})
    raw["edges"] = new_edges
    raw["nodes"] = list(cg.get("nodes") or [])
    meta["canvas_graph"] = raw
    project.meta = meta
    return updated


def save_operator_result(
    project: Project,
    node_key: str,
    *,
    input_paths: list[Path],
    output_paths: list[Path],
    reply_text: str,
    gate_status: str | None = None,
) -> dict[str, Any]:
    from datetime import datetime, timezone

    meta = dict(project.meta or {})
    results = dict(meta.get("gpt_operator_results") or {})
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "inputPaths": [str(p) for p in input_paths],
        "outputPaths": [str(p) for p in output_paths],
        "replyPreview": (reply_text or "")[:2000],
        "gateStatus": gate_status,
    }
    results[node_key] = entry
    meta["gpt_operator_results"] = results
    # mirror last reply into excel_gpt node config for UI
    configs = dict(meta.get("excel_gpt_nodes") or {})
    cur = dict(configs.get(node_key) or {})
    if output_paths:
        cur["lastReplyPath"] = str(output_paths[0])
        cur["lastReplyAt"] = entry["at"]
    if gate_status:
        cur["gateStatus"] = gate_status
    configs[node_key] = cur
    meta["excel_gpt_nodes"] = configs
    project.meta = meta
    return entry


def gate_allows_successors(project: Project, gate_node_key: str) -> bool | None:
    """None — нода без вердикта / не branching-роль; True/False — pass/fail."""
    cfg = operator_config(project, gate_node_key)
    if cfg.get("role") not in BRANCHING_ROLES:
        return None
    meta = project.meta if isinstance(project.meta, dict) else {}
    results = meta.get("gpt_operator_results")
    status = ""
    if isinstance(results, dict):
        entry = results.get(gate_node_key)
        if isinstance(entry, dict):
            status = str(entry.get("gateStatus") or "").strip().lower()
    if not status:
        status = str(cfg.get("gateStatus") or "").strip().lower()
    if status == "pass":
        return True
    if status == "fail":
        return False
    return None


def verdict_edge_blocks(
    project: Project, source_key: str, edge_kind: str
) -> bool | None:
    """Блокирует ли стрелка pass/fail/gate переход.

    None — стрелка не вердиктная (после/файлы/проверка).
    True — блок; False — можно идти.
    """
    kind = normalize_edge_kind(edge_kind)
    if not is_verdict_edge_kind(kind):
        return None
    allowed = gate_allows_successors(project, source_key)
    if allowed is None:
        return True  # нет вердикта → ни одна ветка не открыта
    if is_pass_edge_kind(kind):
        return allowed is not True
    # fail
    return allowed is not False
