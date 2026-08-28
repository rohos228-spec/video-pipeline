"""Группы нод (пресеты канваса): веер scene_design и пользовательские связки.

Определения живут в репозитории — наследуются всеми ПК через git pull.
Встроенные группы — в коде (NODE_GROUPS ниже), пользовательские — JSON-файлы
в ``node_groups/*.json`` в корне репо (создание/переименование/удаление из
палитры Studio). Группа = набор нод (тип, data-маркеры, промпт-вариант,
относительные позиции) + внутренние связи + точки входа/выхода для вшивания
в цепочку существующего канваса. Результаты работ (чекпоинты, ячейки,
реестры) в группу не входят — только структура, промты и флаги meta.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project
from app.orchestrator.default_graph import SD_FANOUT
from app.project_root import find_project_root
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
    # Встроенная (из кода) или пользовательская (node_groups/*.json).
    builtin: bool = True
    # ISO-время последнего обновления (у пользовательских — из JSON).
    updated_at: str | None = None


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
            "4 GPT-агента (персонажи/мир/камера/действие) параллельно "
            "+ сборщик сцен; после каждой ноды — её проверка (Ок/Не ок). "
            "Промты sd_* из 05_excel_gpt выставляются сразу. Нода стиль удалена."
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


# chrono_dyn: скелет → chars/world → action → camera → assemble.
_SD_CHRONO_FANOUT: tuple[tuple[str, str, str], ...] = (
    ("characters", "GPT: персонажи · chrono_dyn", "Паспорта персонажей (вариант chrono_dyn)"),
    ("world", "GPT: мир · chrono_dyn", "Локации из locations_seed (вариант chrono_dyn)"),
    ("action", "GPT: действие · chrono_dyn", "Сцены+фазы — обслуживает скелет"),
    ("camera", "GPT: камера · chrono_dyn", "1 фаза → 1 shot; обслуживает action"),
)


def _scene_design_chrono_dyn_group() -> NodeGroupDef:
    """Веер chrono_dyn: скелет → chars/world → action → camera → assemble."""
    agents: list[GroupNodeSpec] = []
    edges: list[tuple[str, str, str]] = []
    skeleton = GroupNodeSpec(
        local_key="skeleton",
        node_type="excel_gpt",
        label="GPT: скелет · нити",
        description=(
            "Волна 0: карта текста → сцены с нитями/наследием → критик стыков; "
            "один агент, дальше отдаёт детализаторам"
        ),
        preferred_id="n_excel_gpt_sd_cd_skeleton",
        dx=_STEP_X,
        dy=0.0,
        marker="skeleton",
        prompt_variant="sd_skeleton",
    )
    col = {
        "characters": (2, -_FAN_DY),
        "world": (2, _FAN_DY),
        "action": (3, 0.0),
        "camera": (4, 0.0),
    }
    for agent, label, descr in _SD_CHRONO_FANOUT:
        dx_mul, dy = col[agent]
        agents.append(
            GroupNodeSpec(
                local_key=agent,
                node_type="excel_gpt",
                label=label,
                description=descr,
                preferred_id=f"n_excel_gpt_sd_cd_{agent}",
                dx=_STEP_X * dx_mul,
                dy=dy,
                marker=agent,
                prompt_variant=f"sd_{agent}_chrono_dyn",
            )
        )
    for agent in ("characters", "world"):
        edges.append(("skeleton", agent, "after"))
        edges.append((agent, "action", "after"))
    edges.append(("action", "camera", "after"))
    edges.append(("camera", "assemble", "after"))

    asm = GroupNodeSpec(
        local_key="assemble",
        node_type="excel_gpt",
        label="GPT: сборка · chrono_dyn",
        description="Сборщик chrono_dyn: action>camera, фазы→кадры",
        preferred_id="n_excel_gpt_sd_cd_asm",
        dx=_STEP_X * 5,
        dy=0.0,
        marker="assemble",
        prompt_variant="sd_assemble_chrono_dyn",
    )
    return NodeGroupDef(
        group_id="scene_design_fanout_chrono_dyn",
        title="Сцены: скелет + chrono_dyn",
        description=(
            "Скелет (нити/наследие) → персонажи/мир → действие "
            "(сцены+фазы) → камера (1 фаза=1 shot) → сборка. "
            "Промты sd_skeleton + sd_*_chrono_dyn. Нода стиль удалена."
        ),
        category="planning",
        default_after_type="split",
        nodes=(skeleton, *agents, asm),
        internal_edges=tuple(edges),
        entry_keys=("skeleton",),
        exit_key="assemble",
        project_meta={
            "scene_design_enabled": True,
            "scene_design_variant": "chrono_dyn",
            "scene_design_skeleton": True,
        },
        replaces_types=("scene_design",),
        exit_edge_kind="after",
    )


# Рабочая конфигурация GPT-ноды, пишущей в DB через apply-ops (project_file).
_WORK_OPERATOR_CONFIG: dict[str, Any] = {
    "outputMode": "project_file",
    "transport": "api",
}


def _work_spec(
    local_key: str, label: str, descr: str, dx: float, prompt_variant: str
) -> GroupNodeSpec:
    return GroupNodeSpec(
        local_key=local_key,
        node_type="excel_gpt",
        label=label,
        description=descr,
        preferred_id=f"n_excel_gpt_fw_{local_key}",
        dx=dx,
        dy=0.0,
        prompt_variant=prompt_variant,
        slot_overflow=True,  # цепочка вне enrich-слотов 1..5
        operator_config=dict(_WORK_OPERATOR_CONFIG),
    )


def _script_frames_qc_group() -> NodeGroupDef:
    """Сценарист → проверка → действие → кадры T/X → промты → QC."""
    script = _work_spec(
        "script",
        "GPT: сценарист",
        "Целый закадр → 1 seed-ячейка → биты (разбивка ячеек — хвостом группы)",
        _STEP_X,
        "script_writer_ru",
    )
    check_script = GroupNodeSpec(
        local_key="check_script",
        node_type="excel_gpt",
        label="Проверка: сценарий",
        description="Проверка закадра и разбивки по промту сценариста (Ок/Не ок)",
        preferred_id="n_excel_gpt_fw_check_script",
        dx=_STEP_X * 2,
        dy=0.0,
        slot_overflow=True,
        operator_config=dict(_CHECK_OPERATOR_CONFIG),
    )
    action = _work_spec(
        "action",
        "GPT: главное действие · по битам",
        "Биты + закадр → нумерованная цепь сцен; текст НЕ генерирует",
        _STEP_X * 3,
        "main_action_from_bits_ru",
    )
    shots = _work_spec(
        "shots",
        "GPT: сцены → кадры",
        "Цепь сцен → шаблоны T/X (select→shots→drop_order); текст НЕ генерирует",
        _STEP_X * 4,
        "scenes_to_frames_ru",
    )
    frames = _work_spec(
        "frames",
        "GPT: промты кадров · continuity",
        "Разбивка → промт_картинки + промт_видео; сцена = цепь, не слайд-шоу",
        _STEP_X * 5,
        "frame_prompts_continuity_ru",
    )
    qc = _work_spec(
        "qc",
        "GPT: QC промптов",
        "Проверка промптов по блоку и правилам; чинит только нарушения (apply-ops)",
        _STEP_X * 6,
        "prompts_qc_continuity_ru",
    )
    return NodeGroupDef(
        group_id="script_frames_qc",
        title="Сценарий → промпты кадров + QC",
        description=(
            "Сценарист: целый закадр → 1 seed → биты → проверка → "
            "главное действие → кадры по шаблонам T/X → промпты → QC. "
            "Разбивка на ячейки — хвостом группы (vo_shot_expand)."
        ),
        category="planning",
        default_after_type="plan",
        nodes=(script, check_script, action, shots, frames, qc),
        internal_edges=(
            ("script", "check_script", "after"),
            ("check_script", "action", "pass"),
            ("check_script", "script", "fail"),
            ("action", "shots", "after"),
            ("shots", "frames", "after"),
            ("frames", "qc", "after"),
        ),
        entry_keys=("script",),
        exit_key="qc",
        project_meta={},
        exit_edge_kind="after",
    )


NODE_GROUPS: dict[str, NodeGroupDef] = {
    g.group_id: g
    for g in (
        _scene_design_group(),
        _scene_design_chrono_dyn_group(),
        _script_frames_qc_group(),
    )
}

# Категории, которые понимает палитра (см. NODE_CATEGORY_LABELS на фронте).
GROUP_CATEGORIES = (
    "planning",
    "objects",
    "enrich",
    "media",
    "audio",
    "assembly",
    "publish",
    "hitl",
)

_EDGE_KINDS = ("after", "pass", "fail")


# ── Пользовательские группы: node_groups/*.json в корне репо ─────────────
# Файлы коммитятся → наследуются всеми ПК через git pull, как и код.


def custom_groups_dir() -> Path:
    return find_project_root() / "node_groups"


def _group_file(group_id: str) -> Path:
    return custom_groups_dir() / f"{group_id}.json"


def _spec_from_dict(raw: dict[str, Any], *, builtin: bool) -> GroupNodeSpec:
    return GroupNodeSpec(
        local_key=str(raw["local_key"]),
        node_type=str(raw.get("node_type") or "excel_gpt"),
        label=str(raw.get("label") or raw["local_key"]),
        description=str(raw.get("description") or ""),
        preferred_id=str(raw.get("preferred_id") or f"n_{raw['local_key']}"),
        dx=float(raw.get("dx") or 0.0),
        dy=float(raw.get("dy") or 0.0),
        marker=(str(raw["marker"]) if raw.get("marker") else None),
        prompt_variant=(
            str(raw["prompt_variant"]) if raw.get("prompt_variant") else None
        ),
        slot_overflow=bool(raw.get("slot_overflow")),
        operator_config=(
            dict(raw["operator_config"])
            if isinstance(raw.get("operator_config"), dict)
            else None
        ),
    )


def _group_from_dict(raw: dict[str, Any], *, builtin: bool) -> NodeGroupDef:
    nodes = tuple(_spec_from_dict(n, builtin=builtin) for n in raw["nodes"])
    edges = tuple(
        (str(s), str(t), str(k)) for s, t, k in raw.get("internal_edges") or []
    )
    return NodeGroupDef(
        group_id=str(raw["group_id"]),
        title=str(raw.get("title") or raw["group_id"]),
        description=str(raw.get("description") or ""),
        category=str(raw.get("category") or "planning"),
        default_after_type=str(raw.get("default_after_type") or ""),
        nodes=nodes,
        internal_edges=edges,
        entry_keys=tuple(str(k) for k in raw.get("entry_keys") or ()),
        exit_key=str(raw.get("exit_key") or nodes[-1].local_key),
        project_meta=dict(raw.get("project_meta") or {}),
        replaces_types=tuple(str(t) for t in raw.get("replaces_types") or ()),
        exit_edge_kind=str(raw.get("exit_edge_kind") or "after"),
        builtin=builtin,
        updated_at=(str(raw["updated_at"]) if raw.get("updated_at") else None),
    )


def _group_to_dict(g: NodeGroupDef) -> dict[str, Any]:
    return {
        "version": 1,
        "group_id": g.group_id,
        "title": g.title,
        "description": g.description,
        "category": g.category,
        "default_after_type": g.default_after_type,
        "nodes": [
            {
                "local_key": n.local_key,
                "node_type": n.node_type,
                "label": n.label,
                "description": n.description,
                "preferred_id": n.preferred_id,
                "dx": n.dx,
                "dy": n.dy,
                **({"marker": n.marker} if n.marker else {}),
                **({"prompt_variant": n.prompt_variant} if n.prompt_variant else {}),
                **({"slot_overflow": True} if n.slot_overflow else {}),
                **(
                    {"operator_config": n.operator_config}
                    if n.operator_config
                    else {}
                ),
            }
            for n in g.nodes
        ],
        "internal_edges": [list(e) for e in g.internal_edges],
        "entry_keys": list(g.entry_keys),
        "exit_key": g.exit_key,
        "project_meta": g.project_meta,
        **({"replaces_types": list(g.replaces_types)} if g.replaces_types else {}),
        "exit_edge_kind": g.exit_edge_kind,
        "updated_at": g.updated_at,
    }


def validate_group_payload(raw: dict[str, Any]) -> list[str]:
    """Ошибки spec'а группы (пустой список = ок)."""
    errors: list[str] = []
    gid = str(raw.get("group_id") or "").strip()
    if not gid:
        errors.append("group_id пуст")
    elif not re.fullmatch(r"[a-z0-9][a-z0-9_\-]{1,63}", gid):
        errors.append(
            "group_id: только латиница/цифры/дефис/подчёркивание (2–64 символа)"
        )
    if not str(raw.get("title") or "").strip():
        errors.append("title пуст")
    cat = str(raw.get("category") or "planning")
    if cat not in GROUP_CATEGORIES:
        errors.append(f"category {cat!r} не из {sorted(GROUP_CATEGORIES)}")
    nodes = raw.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes: нужна хотя бы одна нода")
        return errors
    keys: list[str] = []
    for i, n in enumerate(nodes):
        if not isinstance(n, dict):
            errors.append(f"nodes[{i}]: не объект")
            continue
        key = str(n.get("local_key") or "").strip()
        if not key:
            errors.append(f"nodes[{i}]: local_key пуст")
        elif key in keys:
            errors.append(f"nodes[{i}]: дубль local_key {key!r}")
        keys.append(key)
        try:
            float(n.get("dx") or 0.0)
            float(n.get("dy") or 0.0)
        except (TypeError, ValueError):
            errors.append(f"nodes[{i}]: dx/dy не числа")
    key_set = set(keys)
    for e in raw.get("internal_edges") or []:
        if not (isinstance(e, (list, tuple)) and len(e) == 3):
            errors.append(f"internal_edges: плохое ребро {e!r}")
            continue
        s, t, k = str(e[0]), str(e[1]), str(e[2])
        if s not in key_set or t not in key_set:
            errors.append(f"internal_edges: {s!r}→{t!r} вне nodes")
        if k not in _EDGE_KINDS:
            errors.append(f"internal_edges: kind {k!r} не из {_EDGE_KINDS}")
    for k in raw.get("entry_keys") or []:
        if str(k) not in key_set:
            errors.append(f"entry_keys: {k!r} вне nodes")
    exit_key = str(raw.get("exit_key") or "")
    if exit_key and exit_key not in key_set:
        errors.append(f"exit_key {exit_key!r} вне nodes")
    return errors


def load_custom_groups() -> dict[str, NodeGroupDef]:
    """Пользовательские группы из node_groups/*.json (без кэша — файлы мелкие)."""
    out: dict[str, NodeGroupDef] = {}
    folder = custom_groups_dir()
    if not folder.is_dir():
        return out
    for path in sorted(folder.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            errs = validate_group_payload(raw)
            if errs:
                logger.warning("node_groups: {} пропущен: {}", path.name, errs)
                continue
            g = _group_from_dict(raw, builtin=False)
            if g.group_id in NODE_GROUPS:
                logger.warning(
                    "node_groups: {} — id {!r} занят встроенной группой, пропуск",
                    path.name,
                    g.group_id,
                )
                continue
            out[g.group_id] = g
        except Exception as e:  # noqa: BLE001 — битый файл не роняет каталог
            logger.warning("node_groups: не смог прочитать {}: {}", path.name, e)
    return out


def all_groups() -> dict[str, NodeGroupDef]:
    """Встроенные + пользовательские (пользовательские перечитываются с диска)."""
    return {**NODE_GROUPS, **load_custom_groups()}


def save_custom_group(raw: dict[str, Any]) -> NodeGroupDef:
    """Создать/перезаписать пользовательскую группу (JSON в репо)."""
    errs = validate_group_payload(raw)
    if errs:
        raise ValueError("спек группы невалиден: " + "; ".join(errs))
    gid = str(raw["group_id"]).strip()
    if gid in NODE_GROUPS:
        raise ValueError(f"group_id {gid!r} занят встроенной группой")
    payload = dict(raw)
    payload["group_id"] = gid
    payload["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    group = _group_from_dict(payload, builtin=False)
    folder = custom_groups_dir()
    folder.mkdir(parents=True, exist_ok=True)
    _group_file(gid).write_text(
        json.dumps(_group_to_dict(group), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("node_groups: сохранена пользовательская группа {}", gid)
    return group


def update_custom_group(group_id: str, patch: dict[str, Any]) -> NodeGroupDef:
    """Обновить название/описание/категорию пользовательской группы."""
    gid = str(group_id or "").strip()
    if gid in NODE_GROUPS:
        raise ValueError(f"группа {gid!r} встроенная — правится только в коде")
    path = _group_file(gid)
    if not path.is_file():
        raise ValueError(f"неизвестная группа {gid!r}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    for key in ("title", "description", "category"):
        if key in patch and patch[key] is not None:
            raw[key] = str(patch[key])
    return save_custom_group(raw)


def delete_custom_group(group_id: str) -> None:
    gid = str(group_id or "").strip()
    if gid in NODE_GROUPS:
        raise ValueError(f"группа {gid!r} встроенная — удалить нельзя")
    path = _group_file(gid)
    if not path.is_file():
        raise ValueError(f"неизвестная группа {gid!r}")
    path.unlink()
    logger.info("node_groups: удалена пользовательская группа {}", gid)


_RU_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


def slugify_group_id(title: str) -> str:
    """«Моя связка GPT» → moya_svyazka_gpt; пусто → group_<hex8>."""
    out: list[str] = []
    for ch in title.strip().lower():
        if ch in _RU_TRANSLIT:
            out.append(_RU_TRANSLIT[ch])
        elif ch.isascii() and (ch.isalnum() or ch in "_-"):
            out.append(ch)
        elif ch in " /":
            out.append("_")
    slug = re.sub(r"_+", "_", "".join(out)).strip("_-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9_\-]{1,63}", slug or ""):
        slug = f"group_{uuid.uuid4().hex[:8]}"
    return slug[:64]


def unique_group_id(base: str) -> str:
    gid = base
    k = 2
    existing = all_groups()
    while gid in existing or _group_file(gid).exists():
        gid = f"{base}_{k}"
        k += 1
    return gid


def list_node_groups() -> list[dict[str, Any]]:
    """Каталог групп для палитры (API): встроенные + пользовательские."""
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
            "builtin": g.builtin,
            "updated_at": g.updated_at,
        }
        for g in all_groups().values()
    ]


def get_node_group(group_id: str) -> NodeGroupDef | None:
    return all_groups().get(str(group_id or "").strip())


def get_group_detail(group_id: str) -> dict[str, Any] | None:
    """Полный spec группы для менеджера: позиции, рёбра, промты, флаги.

    Используется палитрой для визуального превью дизайна группы (мини-канвас)
    и просмотра состава до вставки.
    """
    g = get_node_group(group_id)
    if g is None:
        return None
    return {
        "id": g.group_id,
        "title": g.title,
        "description": g.description,
        "category": g.category,
        "builtin": g.builtin,
        "updated_at": g.updated_at,
        "default_after_type": g.default_after_type,
        "entry_keys": list(g.entry_keys),
        "exit_key": g.exit_key,
        "exit_edge_kind": g.exit_edge_kind,
        "project_meta": dict(g.project_meta),
        "nodes": [
            {
                "key": n.local_key,
                "label": n.label,
                "type": n.node_type,
                "description": n.description,
                "dx": n.dx,
                "dy": n.dy,
                "marker": n.marker,
                "prompt_variant": n.prompt_variant,
                "slot_overflow": n.slot_overflow,
                "has_operator_config": n.operator_config is not None,
            }
            for n in g.nodes
        ],
        "internal_edges": [
            {"source": s, "target": t, "kind": k} for s, t, k in g.internal_edges
        ],
    }


async def backfill_group_stamps(session: AsyncSession) -> dict[str, int]:
    """Проставить groupId/groupTitle на нодах веера scene_design в старых проектах.

    Рамка группы на канвасе рисуется по ``data.groupId``; веер, вставленный
    до появления штампов, рамки не имеет. Идемпотентно: трогаем только ноды
    без ``groupId``. Агенты/сборщик — по маркеру sd_agent, проверки — по
    префиксу id ``n_excel_gpt_sd_check_`` (preferred-id группы + суффиксы).
    """
    from sqlalchemy import select

    fanout = NODE_GROUPS["scene_design_fanout"]
    projects = (await session.execute(select(Project))).scalars().all()
    stamped_projects = 0
    stamped_nodes = 0
    for p in projects:
        meta = p.meta if isinstance(p.meta, dict) else None
        if not meta:
            continue
        cg = meta.get("canvas_graph")
        if not isinstance(cg, dict):
            continue
        nodes = cg.get("nodes")
        if not isinstance(nodes, list):
            continue
        changed = False
        for n in nodes:
            if not isinstance(n, dict):
                continue
            data = n.get("data")
            if not isinstance(data, dict) or data.get("groupId"):
                continue
            nid = str(n.get("id") or "")
            if sd_agent_marker(n) is None and not nid.startswith(
                "n_excel_gpt_sd_check_"
            ):
                continue
            data["groupId"] = fanout.group_id
            data["groupTitle"] = fanout.title
            changed = True
            stamped_nodes += 1
        if changed:
            p.meta = dict(meta)
            stamped_projects += 1
    if stamped_projects:
        await session.commit()
        logger.info(
            "node_groups backfill: штампы scene_design_fanout в {} проектах ({} нод)",
            stamped_projects,
            stamped_nodes,
        )
    return {"projects": stamped_projects, "nodes": stamped_nodes}


async def group_from_canvas(
    session: AsyncSession,
    project: Project,
    node_ids: list[str],
    *,
    title: str,
    description: str = "",
    category: str = "planning",
    group_id: str | None = None,
) -> NodeGroupDef:
    """Собрать пользовательскую группу из выделенных нод канваса проекта.

    В группу уходят: типы нод, подписи/описания, data-маркеры (sd_agent,
    slotOverflow), промпт-варианты (meta.prompt_slot_variants), конфиги
    «Работы с GPT» (meta.excel_gpt_nodes), относительные позиции и связи
    внутри выделения (с kind). Результаты работ не копируются.
    """
    ids = [str(i).strip() for i in node_ids if str(i).strip()]
    if not ids:
        raise ValueError("не выбрано ни одной ноды")

    meta = dict(project.meta or {})
    graph = canvas_graph_from_meta(meta)
    if graph is None:
        from sqlalchemy import select

        from app.models import Workflow

        wf = (
            await session.execute(
                select(Workflow).where(Workflow.is_default.is_(True))
            )
        ).scalars().first()
        if wf is None:
            raise ValueError("group_from_canvas: нет ни canvas_graph, ни workflow")
        nodes = [dict(n) for n in (wf.nodes or [])]
        edges = [dict(e) for e in (wf.edges or [])]
    else:
        nodes = [dict(n) for n in graph["nodes"]]
        edges = [dict(e) for e in graph["edges"]]

    by_id = {str(n.get("id")): n for n in nodes}
    missing = [i for i in ids if i not in by_id]
    if missing:
        raise ValueError(f"ноды не найдены на канвасе: {missing}")
    selected = [by_id[i] for i in ids]
    id_set = set(ids)

    min_x = min(float((n.get("position") or {}).get("x", 0.0)) for n in selected)
    avg_y = sum(float((n.get("position") or {}).get("y", 0.0)) for n in selected) / len(
        selected
    )

    variants = meta.get("prompt_slot_variants")
    variants = variants if isinstance(variants, dict) else {}
    egn = meta.get("excel_gpt_nodes")
    egn = egn if isinstance(egn, dict) else {}

    specs: list[GroupNodeSpec] = []
    for n in selected:
        nid = str(n["id"])
        data = n.get("data") or {}
        pos = n.get("position") or {}
        variant = variants.get(nid)
        prompt_variant = None
        if isinstance(variant, dict) and isinstance(variant.get("main"), str):
            prompt_variant = variant["main"]
        cfg = egn.get(nid)
        specs.append(
            GroupNodeSpec(
                local_key=nid,
                node_type=str(n.get("type") or "excel_gpt"),
                label=str(data.get("label") or nid),
                description=str(data.get("description") or ""),
                preferred_id=nid,
                dx=round(float(pos.get("x", 0.0)) - min_x, 2),
                dy=round(float(pos.get("y", 0.0)) - avg_y, 2),
                marker=sd_agent_marker(n),
                prompt_variant=prompt_variant,
                slot_overflow=data.get("slotOverflow") is True,
                operator_config=dict(cfg) if isinstance(cfg, dict) else None,
            )
        )

    internal: list[tuple[str, str, str]] = []
    for e in edges:
        src, tgt = str(e.get("source")), str(e.get("target"))
        if src in id_set and tgt in id_set:
            kind = str((e.get("data") or {}).get("kind") or "after")
            internal.append((src, tgt, kind if kind in _EDGE_KINDS else "after"))

    # Входы: выделенные ноды с внешними входящими; выход — с внешними исходящими.
    incoming_ext: dict[str, str] = {}  # node_id → тип внешнего источника
    outgoing_ext: set[str] = set()
    for e in edges:
        src, tgt = str(e.get("source")), str(e.get("target"))
        if tgt in id_set and src not in id_set and tgt not in incoming_ext:
            incoming_ext[tgt] = str(by_id.get(src, {}).get("type") or "")
        if src in id_set and tgt not in id_set:
            outgoing_ext.add(src)

    by_x = sorted(selected, key=lambda n: float((n.get("position") or {}).get("x", 0)))
    entry_keys = tuple(
        str(n["id"]) for n in by_x if str(n["id"]) in incoming_ext
    ) or (str(by_x[0]["id"]),)
    exit_key = (
        str(max(outgoing_ext, key=lambda i: float((by_id[i].get("position") or {}).get("x", 0))))
        if outgoing_ext
        else str(by_x[-1]["id"])
    )
    default_after_type = next(
        (t for t in (incoming_ext.get(k) for k in entry_keys) if t), ""
    )

    gid = unique_group_id(slugify_group_id(group_id or title))
    raw = {
        "group_id": gid,
        "title": title.strip(),
        "description": description.strip(),
        "category": category if category in GROUP_CATEGORIES else "planning",
        "default_after_type": default_after_type,
        "nodes": [
            {
                "local_key": s.local_key,
                "node_type": s.node_type,
                "label": s.label,
                "description": s.description,
                "preferred_id": s.preferred_id,
                "dx": s.dx,
                "dy": s.dy,
                **({"marker": s.marker} if s.marker else {}),
                **({"prompt_variant": s.prompt_variant} if s.prompt_variant else {}),
                **({"slot_overflow": True} if s.slot_overflow else {}),
                **(
                    {"operator_config": s.operator_config} if s.operator_config else {}
                ),
            }
            for s in specs
        ],
        "internal_edges": [list(e) for e in internal],
        "entry_keys": list(entry_keys),
        "exit_key": exit_key,
        "project_meta": {},
        "exit_edge_kind": "after",
    }
    return save_custom_group(raw)


def _side_sink_ids(nodes: list[dict]) -> set[str]:
    """Ноды-приёмники «сбоку» (storage/topic/excel_feed/shot_menu) — не цепочка."""
    from app.orchestrator.node_registry import SIDE_NODE_TYPES

    side = set(SIDE_NODE_TYPES)
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


def upgrade_script_frames_qc_graph(meta: dict[str, Any]) -> bool:
    """Старая цепочка check→frames: вставить action+shots (T/X) между ними."""
    graph = canvas_graph_from_meta(meta)
    if graph is None:
        return False
    nodes = [dict(n) for n in graph["nodes"]]
    edges = [dict(e) for e in graph["edges"]]
    by_id = {str(n.get("id")): n for n in nodes}
    check_id = "n_excel_gpt_fw_check_script"
    action_id = "n_excel_gpt_fw_action"
    shots_id = "n_excel_gpt_fw_shots"
    frames_id = "n_excel_gpt_fw_frames"
    if check_id not in by_id or frames_id not in by_id:
        return False
    if action_id in by_id and shots_id in by_id:
        return False
    # есть прямое check→frames (pass/after) — перешить
    direct = [
        e
        for e in edges
        if str(e.get("source")) == check_id and str(e.get("target")) == frames_id
    ]
    if not direct and (action_id in by_id or shots_id in by_id):
        return False
    check = by_id[check_id]
    frames = by_id[frames_id]
    cx = float((check.get("position") or {}).get("x") or 0)
    cy = float((check.get("position") or {}).get("y") or 0)
    fx = float((frames.get("position") or {}).get("x") or (cx + _STEP_X * 3))
    group = _script_frames_qc_group()
    action_spec = next(s for s in group.nodes if s.local_key == "action")
    shots_spec = next(s for s in group.nodes if s.local_key == "shots")
    gid = str((check.get("data") or {}).get("groupId") or "script_frames_qc")
    gtitle = str(
        (check.get("data") or {}).get("groupTitle") or group.title
    )

    def _mk(spec: GroupNodeSpec, x: float) -> dict[str, Any]:
        return {
            "id": spec.preferred_id,
            "type": spec.node_type,
            "position": {"x": x, "y": cy},
            "data": {
                "label": spec.label,
                "description": spec.description,
                "slotOverflow": True,
                "groupId": gid,
                "groupTitle": gtitle,
            },
        }

    if action_id not in by_id:
        nodes.append(_mk(action_spec, cx + (fx - cx) / 3))
    if shots_id not in by_id:
        nodes.append(_mk(shots_spec, cx + 2 * (fx - cx) / 3))
    edges = [
        e
        for e in edges
        if not (
            str(e.get("source")) == check_id and str(e.get("target")) == frames_id
        )
    ]
    need = [
        (check_id, action_id, "pass"),
        (action_id, shots_id, "after"),
        (shots_id, frames_id, "after"),
    ]
    have = {(str(e.get("source")), str(e.get("target"))) for e in edges}
    for src, tgt, kind in need:
        if (src, tgt) in have:
            continue
        e: dict[str, Any] = {
            "id": f"e_{src}_{tgt}",
            "source": src,
            "target": tgt,
            "sourceHandle": "out",
            "targetHandle": "in",
            "data": {"kind": kind},
        }
        if kind == "pass":
            e["label"] = "Ок"
            e["data"]["label"] = "Ок"
        edges.append(e)
    variants = meta.get("prompt_slot_variants")
    variants = dict(variants) if isinstance(variants, dict) else {}
    variants[action_id] = {"main": "main_action_from_bits_ru"}
    variants[shots_id] = {"main": "scenes_to_frames_ru"}
    meta["prompt_slot_variants"] = variants
    egn = meta.get("excel_gpt_nodes")
    egn = dict(egn) if isinstance(egn, dict) else {}
    egn[action_id] = dict(_WORK_OPERATOR_CONFIG)
    egn[shots_id] = dict(_WORK_OPERATOR_CONFIG)
    meta["excel_gpt_nodes"] = egn
    meta["canvas_graph"] = build_canvas_graph_payload(
        workflow_id=int(graph.get("workflow_id") or 0),
        nodes=nodes,
        edges=edges,
    )
    return True


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
            f"неизвестная группа {group_id!r}; есть: {sorted(all_groups())}"
        )
    if group_id == "script_frames_qc":
        meta0 = dict(project.meta or {})
        if upgrade_script_frames_qc_graph(meta0):
            project.meta = meta0
            await session.flush()
            await sync_run_snapshot_from_canvas_graph(session, project, force=True)
            await session.commit()
            logger.info("[#{}] upgrade script_frames_qc: +action +shots", project.id)
            return {
                "group": group_id,
                "upgraded": True,
                "nodes": [
                    "n_excel_gpt_fw_action",
                    "n_excel_gpt_fw_shots",
                ],
            }

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
    # Повторная вставка группы без маркеров разрешена — но у каждой копии
    # свой instance-id (group_id#2, #3…), чтобы рамки копий не сливались.
    instance_id = group.group_id
    copies = {
        str((n.get("data") or {}).get("groupId")).split("#")[0]
        for n in nodes
        if isinstance(n.get("data"), dict) and (n.get("data") or {}).get("groupId")
    }
    if group.group_id in copies:
        k = 2
        while f"{group.group_id}#{k}" in {
            str((n.get("data") or {}).get("groupId"))
            for n in nodes
            if isinstance(n.get("data"), dict)
            and (n.get("data") or {}).get("groupId")
        }:
            k += 1
        instance_id = f"{group.group_id}#{k}"

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
        data: dict[str, Any] = {
            "label": spec.label,
            "description": spec.description,
            # Принадлежность к импортированной группе — обводка на канвасе.
            "groupId": instance_id,
            "groupTitle": group.title,
        }
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
