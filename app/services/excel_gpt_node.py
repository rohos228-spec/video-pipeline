"""Универсальная нода «Работа с GPT» (excel_gpt)."""

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


def is_legacy_enrich_label(label: str | None) -> bool:
    """Старые подписи enrich_1..5 / «Доп работа с Excel» — перезаписываем при миграции."""
    if not label or not str(label).strip():
        return False
    low = str(label).strip().lower()
    markers = (
        "дополнение",
        "доп работа",
        "доп. работа",
        "доп. excel",
        "enrich",
        "excel #",
    )
    return any(m in low for m in markers)


def default_excel_gpt_label(_slot: int) -> str:
    return "Работа с GPT"


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
        label = str(data.get("label") or "").strip()
        if not label or is_legacy_enrich_label(label):
            data["label"] = default_excel_gpt_label(i)
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
            label = str(data.get("label") or "").strip()
            if not label or is_legacy_enrich_label(label):
                data["label"] = default_excel_gpt_label(slot or 1)
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


def slot_from_ready_status(status: ProjectStatus) -> int | None:
    for idx, (_running, ready, _code) in _SLOT_MAP.items():
        if status is ready:
            return idx
    return None


def next_excel_gpt_slot_after_ready(project: Project, ready_status: ProjectStatus) -> int | None:
    """Следующий slot excel_gpt на канвасе после enrich_N_ready (игнор «готово»).

    Stale enrich_completed_slots / excel_gpt_completed_keys не должны давать
    прыжок enrich_2_ready → generating_hero, пока на канвасе есть слот 3+.
    """
    finished = slot_from_ready_status(ready_status)
    if finished is None:
        return None
    nodes = excel_gpt_nodes_from_project(project)
    if not nodes:
        return None
    later = sorted(
        {
            slot_index_from_node(n)
            for n in nodes
            if slot_index_from_node(n) > finished
        }
    )
    return later[0] if later else None


def next_excel_gpt_running_after_ready(
    project: Project, ready_status: ProjectStatus
) -> ProjectStatus | None:
    """Running-статус следующего excel_gpt после enrich_N_ready, если слот есть."""
    nxt = next_excel_gpt_slot_after_ready(project, ready_status)
    if nxt is None:
        return None
    return running_status_for_slot(nxt)


def prepare_enrich_chain_for_auto_advance(
    project: Project, ready_status: ProjectStatus
) -> ProjectStatus | None:
    """Перед auto_advance с enrich_N_ready: цепочка + сброс stale «готово».

    Возвращает enriching_{N+1} если на канвасе есть следующий слот, иначе None
    (тогда caller идёт по обычному graph.next).
    """
    finished = slot_from_ready_status(ready_status)
    if finished is None:
        return None
    nxt_slot = next_excel_gpt_slot_after_ready(project, ready_status)
    if nxt_slot is None:
        return None
    ensure_enrich_auto_chain_to(project, finished)
    clear_excel_gpt_tail_completion(project, finished + 1)
    meta = dict(project.meta or {})
    node_key = resolve_excel_gpt_node_key_for_slot(project, nxt_slot)
    if node_key:
        meta["active_excel_gpt_node_key"] = node_key
        project.meta = meta
    return running_status_for_slot(nxt_slot)


def active_excel_gpt_node_key(project: Project) -> str | None:
    meta = project.meta if isinstance(project.meta, dict) else {}
    key = str(meta.get("active_excel_gpt_node_key") or "").strip()
    return key or None


def excel_gpt_nodes_from_project(project: Project) -> list[dict[str, Any]]:
    """Ноды excel_gpt из canvas_graph meta (fallback — пусто)."""
    from app.services.canvas_graph import canvas_graph_from_meta

    meta = project.meta if isinstance(project.meta, dict) else {}
    cg = canvas_graph_from_meta(meta)
    nodes = list((cg or {}).get("nodes") or [])
    return [n for n in nodes if is_excel_gpt_node_type(str(n.get("type") or ""))]


def resolve_excel_gpt_node_key_for_slot(
    project: Project,
    slot: int,
    *,
    nodes: list[dict[str, Any]] | None = None,
) -> str | None:
    """Найти node_key excel_gpt для слота 1..5 (слева направо / slotIndex)."""
    if not (1 <= int(slot) <= MAX_EXCEL_GPT_SLOTS):
        return None
    pool = nodes if nodes is not None else excel_gpt_nodes_from_project(project)
    if not pool:
        return None
    # Prefer explicit slotIndex match.
    for n in pool:
        if slot_index_from_node(n) == slot:
            nid = str(n.get("id") or "").strip()
            if nid:
                return nid
    # Fallback: order by x position.
    ordered = sorted(
        pool,
        key=lambda n: float((n.get("position") or {}).get("x", 0)),
    )
    if slot - 1 < len(ordered):
        nid = str(ordered[slot - 1].get("id") or "").strip()
        return nid or None
    return None


def active_node_key(project: Project) -> str | None:
    meta = project.meta if isinstance(project.meta, dict) else {}
    key = meta.get("active_excel_gpt_node_key")
    return str(key) if key else None


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


def completed_node_keys(project: Project) -> set[str]:
    meta = project.meta if isinstance(project.meta, dict) else {}
    raw = meta.get("excel_gpt_completed_keys") or []
    return {str(k) for k in raw if k}


def max_excel_gpt_slot(project: Project) -> int:
    """Максимальный slotIndex среди excel_gpt на канвасе (1..5)."""
    nodes = excel_gpt_nodes_from_project(project)
    if not nodes:
        return 1
    return max(slot_index_from_node(n) for n in nodes)


def next_incomplete_excel_gpt_slot(project: Project, after_slot: int) -> int | None:
    """Следующий слот excel_gpt после after_slot (для цепочки / gen_queue bypass).

    При активном enrich_auto_chain_to возвращает следующий слот даже если
    он уже в completed — старт должен перегенерировать.
    """
    nodes = excel_gpt_nodes_from_project(project)
    if not nodes:
        return None
    meta = project.meta if isinstance(project.meta, dict) else {}
    chain_to = meta.get("enrich_auto_chain_to")
    done_slots = set()
    for raw in meta.get("enrich_completed_slots") or []:
        try:
            done_slots.add(int(raw))
        except (TypeError, ValueError):
            continue
    done_keys = completed_node_keys(project)
    candidates: list[int] = []
    for n in nodes:
        slot = slot_index_from_node(n)
        if slot <= after_slot:
            continue
        nid = str(n.get("id") or "")
        marked_done = slot in done_slots or (nid and nid in done_keys)
        if marked_done and not (
            isinstance(chain_to, int) and slot <= chain_to
        ):
            continue
        candidates.append(slot)
    if candidates:
        return min(candidates)
    if isinstance(chain_to, int) and after_slot < chain_to:
        nxt = after_slot + 1
        if any(slot_index_from_node(n) == nxt for n in nodes):
            return nxt
    return None


def ensure_enrich_auto_chain_to(project: Project, from_slot: int) -> int | None:
    """Выставить meta.enrich_auto_chain_to = max slot, если на канвасе есть хвост.

    Нужно, чтобы после enrich_N_ready сразу шёл enriching_N+1 без ожидания
    auto_advance (который часто режется gen_queue / auto_mode=False).
    """
    max_slot = max_excel_gpt_slot(project)
    if max_slot <= from_slot:
        return None
    meta = dict(project.meta or {})
    cur = meta.get("enrich_auto_chain_to")
    if isinstance(cur, int) and cur >= max_slot:
        return cur
    meta["enrich_auto_chain_to"] = max_slot
    project.meta = meta
    return max_slot


def clear_excel_gpt_tail_completion(
    project: Project,
    from_slot: int,
) -> dict[str, Any]:
    """Снять enrich_completed / excel_gpt_completed_keys для слотов >= from_slot.

    Старт/цепочка должны перегенерировать даже «готовые» ноды.
    """
    if from_slot < 1:
        from_slot = 1
    nodes = excel_gpt_nodes_from_project(project)
    slots_to_clear: set[int] = set()
    keys_to_clear: set[str] = set()
    for n in nodes:
        slot = slot_index_from_node(n)
        if slot < from_slot:
            continue
        slots_to_clear.add(slot)
        nid = str(n.get("id") or "").strip()
        if nid:
            keys_to_clear.add(nid)

    meta = dict(project.meta or {})
    completed = [
        int(x) for x in (meta.get("enrich_completed_slots") or []) if str(x).isdigit()
    ]
    slots_cleared = [s for s in completed if s in slots_to_clear]
    if slots_cleared:
        meta["enrich_completed_slots"] = sorted(
            s for s in completed if s not in slots_to_clear
        )

    keys = [str(k) for k in (meta.get("excel_gpt_completed_keys") or [])]
    keys_cleared = [k for k in keys if k in keys_to_clear]
    if keys_cleared:
        meta["excel_gpt_completed_keys"] = [k for k in keys if k not in keys_to_clear]

    if slots_cleared or keys_cleared:
        project.meta = meta
    return {
        "from_slot": from_slot,
        "slots_cleared": slots_cleared,
        "keys_cleared": keys_cleared,
    }


def excel_gpt_force_rerun_slots(project: Project) -> set[int]:
    """Слоты, которые нельзя пропускать как done (активная цепочка)."""
    meta = project.meta if isinstance(project.meta, dict) else {}
    chain_to = meta.get("enrich_auto_chain_to")
    if not isinstance(chain_to, int):
        return set()
    return set(range(1, min(5, chain_to) + 1))


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
