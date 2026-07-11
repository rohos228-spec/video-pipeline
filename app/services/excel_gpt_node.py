"""Универсальная нода «Доп работа с Excel» (excel_gpt)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from app.models import Project, ProjectStatus

InputSource = Literal["project_xlsx", "upload", "voiceover"]

EXCEL_GPT_STEP_CODE = "excel_gpt"
EXCEL_GPT_NODE_TYPE = "excel_gpt"
MAX_EXCEL_GPT_SLOTS = 5

_SLOT_MAP: dict[int, tuple[ProjectStatus, ProjectStatus, str]] = {
    1: (ProjectStatus.enriching_1, ProjectStatus.enrich_1_ready, "enrich_1"),
    2: (ProjectStatus.enriching_2, ProjectStatus.enrich_2_ready, "enrich_2"),
    3: (ProjectStatus.enriching_3, ProjectStatus.enrich_3_ready, "enrich_3"),
    4: (ProjectStatus.enriching_4, ProjectStatus.enrich_4_ready, "enrich_4"),
    5: (ProjectStatus.enriching_5, ProjectStatus.enrich_5_ready, "enrich_5"),
}


def is_excel_gpt_node_type(node_type: str) -> bool:
    return node_type == EXCEL_GPT_NODE_TYPE or (
        node_type.startswith("enrich_") and node_type != "enrich"
    )


def legacy_enrich_slot_from_type(node_type: str) -> int | None:
    if not node_type.startswith("enrich_"):
        return None
    tail = node_type.removeprefix("enrich_")
    try:
        idx = int(tail)
    except ValueError:
        return None
    return idx if 1 <= idx <= MAX_EXCEL_GPT_SLOTS else None


def slot_index_from_node(node: dict[str, Any]) -> int:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    raw = data.get("slotIndex")
    if isinstance(raw, int) and 1 <= raw <= MAX_EXCEL_GPT_SLOTS:
        return raw
    typ = str(node.get("type") or "")
    legacy = legacy_enrich_slot_from_type(typ)
    if legacy is not None:
        return legacy
    return 1


def assign_slot_indices(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Нумерует excel_gpt ноды слева направо (1..5)."""
    excel_nodes = sorted(
        [n for n in nodes if is_excel_gpt_node_type(str(n.get("type") or ""))],
        key=lambda n: float((n.get("position") or {}).get("x", 0)),
    )
    out: list[dict[str, Any]] = []
    for i, n in enumerate(excel_nodes[:MAX_EXCEL_GPT_SLOTS], start=1):
        data = dict(n.get("data") or {})
        data["slotIndex"] = i
        if not data.get("label"):
            data["label"] = f"Доп. Excel #{i}"
        out.append({**n, "data": data, "type": EXCEL_GPT_NODE_TYPE})
    keyed = {n["id"]: n for n in out}
    result: list[dict[str, Any]] = []
    for n in nodes:
        nid = n.get("id")
        if nid in keyed:
            result.append(keyed[nid])
            continue
        typ = str(n.get("type") or "")
        if is_excel_gpt_node_type(typ):
            continue
        result.append(n)
    return result


def migrate_enrich_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    migrated: list[dict[str, Any]] = []
    for n in nodes:
        typ = str(n.get("type") or "")
        if is_excel_gpt_node_type(typ) and typ != EXCEL_GPT_NODE_TYPE:
            data = dict(n.get("data") or {})
            slot = legacy_enrich_slot_from_type(typ)
            if slot is not None:
                data.setdefault("slotIndex", slot)
            if not data.get("label"):
                data["label"] = f"Доп. Excel #{slot or 1}"
            migrated.append({**n, "type": EXCEL_GPT_NODE_TYPE, "data": data})
        else:
            migrated.append(n)
    return assign_slot_indices(migrated)


def running_status_for_slot(slot: int) -> ProjectStatus:
    return _SLOT_MAP[slot][0]


def ready_status_for_slot(slot: int) -> ProjectStatus:
    return _SLOT_MAP[slot][1]


def slot_from_running_status(status: ProjectStatus) -> int | None:
    for idx, (running, _ready, _code) in _SLOT_MAP.items():
        if status is running:
            return idx
    return None


def node_config(project: Project, node_key: str) -> dict[str, Any]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    configs = meta.get("excel_gpt_nodes")
    if not isinstance(configs, dict):
        return {}
    cfg = configs.get(node_key)
    return dict(cfg) if isinstance(cfg, dict) else {}


def input_source(project: Project, node_key: str | None) -> InputSource:
    if not node_key:
        return "project_xlsx"
    cfg = node_config(project, node_key)
    raw = str(cfg.get("inputSource") or "project_xlsx")
    if raw in ("project_xlsx", "upload", "voiceover"):
        return raw  # type: ignore[return-value]
    return "project_xlsx"


def upload_dir(project: Project, node_key: str) -> Path:
    return project.data_dir / "excel_gpt_uploads" / node_key


def upload_file_path(project: Project, node_key: str, filename: str) -> Path:
    safe = Path(filename).name
    return upload_dir(project, node_key) / safe


def active_node_key(project: Project) -> str | None:
    meta = project.meta if isinstance(project.meta, dict) else {}
    key = meta.get("active_excel_gpt_node_key")
    return str(key) if key else None


def completed_node_keys(project: Project) -> set[str]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    raw = meta.get("excel_gpt_completed_keys") or []
    return {str(k) for k in raw if k}


def display_attachment_name(project: Project, node_key: str | None) -> str:
    src = input_source(project, node_key)
    if src == "voiceover":
        return "voiceover.txt"
    if src == "upload" and node_key:
        cfg = node_config(project, node_key)
        name = str(cfg.get("uploadedFileName") or "").strip()
        return name or "upload.xlsx"
    return "project.xlsx"


def attachment_paths(project: Project, node_key: str | None = None) -> list[Path]:
    """Файлы данных для отправки в GPT (без промта)."""
    key = node_key or active_node_key(project)
    src = input_source(project, key)
    paths: list[Path] = []
    if src == "voiceover":
        voice = project.data_dir / "voiceover.txt"
        if voice.is_file():
            paths.append(voice)
        return paths
    if src == "upload" and key:
        cfg = node_config(project, key)
        fname = str(cfg.get("uploadedFileName") or "").strip()
        if fname:
            p = upload_file_path(project, key, fname)
            if p.is_file():
                paths.append(p)
        return paths
    xlsx = project.data_dir / "project.xlsx"
    if xlsx.is_file():
        paths.append(xlsx)
    return paths


async def clear_slot_completion_meta(
    session: Any,
    project: Project,
    slot: int,
    *,
    node_key: str | None = None,
) -> dict[str, Any]:
    """Сбросить enrich_completed_slots и excel_gpt_completed_keys для слота."""
    from app.orchestrator.graph.planner import load_graph_for_project

    meta = dict(project.meta or {})
    completed = [int(x) for x in (meta.get("enrich_completed_slots") or []) if str(x).isdigit()]
    slots_cleared: list[int] = []
    if slot in completed:
        completed.remove(slot)
        slots_cleared.append(slot)
        meta["enrich_completed_slots"] = sorted(completed)

    keys = [str(k) for k in (meta.get("excel_gpt_completed_keys") or [])]
    keys_cleared: list[str] = []
    targets: set[str] = set()
    if node_key:
        targets.add(str(node_key))
    graph = await load_graph_for_project(session, project)
    for nid, n in graph._by_id.items():
        if str(n.get("type") or "") != EXCEL_GPT_NODE_TYPE:
            continue
        if slot_index_from_node(n) == slot:
            targets.add(str(nid))
    for k in list(keys):
        if k in targets:
            keys.remove(k)
            keys_cleared.append(k)
    meta["excel_gpt_completed_keys"] = keys
    if node_key and str(meta.get("active_excel_gpt_node_key") or "") == str(node_key):
        meta.pop("active_excel_gpt_node_key", None)
    project.meta = meta
    return {
        "slot": slot,
        "slots_cleared": slots_cleared,
        "keys_cleared": keys_cleared,
    }


def remap_node_keys_in_meta(project: Project, mapping: dict[str, str]) -> list[str]:
    """Перенести excel_gpt_nodes и upload-файлы при смене node_key (paste)."""
    import shutil

    meta = dict(project.meta or {})
    configs = dict(meta.get("excel_gpt_nodes") or {})
    remapped: list[str] = []
    for old_key, new_key in mapping.items():
        old_s, new_s = str(old_key), str(new_key)
        if not old_s or not new_s or old_s == new_s:
            continue
        cfg = configs.pop(old_s, None)
        if cfg:
            configs[new_s] = dict(cfg)
            remapped.append(new_s)
        old_dir = upload_dir(project, old_s)
        if old_dir.is_dir():
            new_dir = upload_dir(project, new_s)
            new_dir.mkdir(parents=True, exist_ok=True)
            for f in old_dir.iterdir():
                if f.is_file():
                    shutil.copy2(f, new_dir / f.name)
    active = meta.get("active_excel_gpt_node_key")
    if active and str(active) in mapping:
        meta["active_excel_gpt_node_key"] = mapping[str(active)]
    done_keys = [str(k) for k in (meta.get("excel_gpt_completed_keys") or [])]
    meta["excel_gpt_completed_keys"] = [mapping.get(k, k) for k in done_keys]
    meta["excel_gpt_nodes"] = configs
    project.meta = meta
    return remapped
