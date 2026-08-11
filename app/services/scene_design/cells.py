"""Staging-ячейки scene_design: срез агента → атомарные проверенные факты.

Каждый срез категорийного агента конвертируется в ячейки
``SceneDesignCell`` (агент → тип сущности → ключ → поле → значение).
Валидация — при записи: цитаты ищутся в полном закадре (заодно считается
``vo_offset`` для хронологии), enum-поля сверяются со словарями. Битые
ячейки не теряются: пишутся со ``status="rejected"`` и причиной, но в
сборку не идут. Запись — частями (chunk), чтобы не держать длинную
транзакцию SQLite.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from typing import Any

from loguru import logger
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, SceneDesignCell

_CHUNK = 100

# Словари допустимых значений (из docs/scene_grammar_unified_agent_v1.md §I).
SHOT_FEATURES: frozenset[str] = frozenset({
    "Крупный план лица", "Сверхкрупный план глаз", "Средний план персонажа",
    "Общий план локации", "Панорамный вид", "Вид сверху", "Вид с дрона",
    "Вид от первого лица", "Силуэт", "Групповая сцена", "Массовка",
    "Экшен-сцена", "Диалог", "Интерьер", "Экстерьер", "Деталь объекта",
    "Макросъемка", "Динамическая камера", "Статичная композиция",
    "Симметричная композиция", "Контровой свет", "Ночной кадр",
    "Закатный кадр", "Рассветный кадр", "Атмосферный туман",
    "Историческая реконструкция", "Документальная хроника",
    "Замедленное действие", "Демонстрация предмета",
    "Художественная постановка", "Портретный кадр", "Бытовая сцена",
    "Сцена природы", "Сцена разрушения", "Сцена катастрофы",
    "Архитектурный акцент",
})
SCENE_STRUCTURES: frozenset[str] = frozenset(
    {
        "continuity",
        "associative",
        "parallel",
        "developing",
        "opening",
        "closing",
        "climax",
    }
)
JOINT_TYPES: frozenset[str] = frozenset(
    {
        "action",
        "screen_position",
        "form",
        "concept",
        "combined",
        "none",
        "object",
        "sound",
        "eyeline",
        "match",
    }
)
TRANSITIONS: frozenset[str] = frozenset(
    {
        "cut",
        "dissolve",
        "fade",
        "wipe",
        "smash",
        "exit_frame",
        "j_cut",
        "l_cut",
        # chrono_dyn / cinematic glue
        "match_cut",
        "cut_on_action",
        "eyeline",
        "insert_bridge",
        "sound_bridge",
    }
)

_ID_RE = {
    "character": re.compile(r"^c\d{2,}$"),
    "location": re.compile(r"^loc\d{2,}$"),
}


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().casefold())


def _vo_offset(full_vo_norm: str, quote: str) -> int | None:
    """Смещение цитаты в нормализованном закадре (None — не нашлась)."""
    q = _norm(quote)
    if not q:
        return None
    idx = full_vo_norm.find(q)
    return idx if idx != -1 else None


def _dump(value: Any) -> str:
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value if value is not None else "").strip()


def _cell(
    project: Project,
    agent: str,
    kind: str,
    target_key: str,
    field: str,
    value: Any,
    *,
    seq: int,
    vo_offset: int | None = None,
) -> SceneDesignCell:
    return SceneDesignCell(
        project_id=project.id,
        agent=agent,
        kind=kind,
        target_key=target_key,
        field=field,
        value=_dump(value),
        seq=seq,
        vo_offset=vo_offset,
        status="ok",
    )


def _reject(cell: SceneDesignCell, reason: str) -> SceneDesignCell:
    cell.status = "rejected"
    cell.error = reason[:300]
    return cell


def _item_fields(
    project: Project,
    agent: str,
    kind: str,
    key: str,
    item: dict[str, Any],
    fields: Iterable[str],
    *,
    quote_field: str | None,
    full_vo_norm: str,
) -> list[SceneDesignCell]:
    """Общий конвертер: dict → ячейки по списку полей (+vo_offset по цитате)."""
    cells: list[SceneDesignCell] = []
    offset: int | None = None
    quote_missing = False
    if quote_field:
        quote = str(item.get(quote_field) or "")
        offset = _vo_offset(full_vo_norm, quote)
        quote_missing = offset is None
    for seq, field in enumerate(fields):
        value = item.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        cell = _cell(
            project, agent, kind, key, field, value, seq=seq, vo_offset=offset
        )
        if field == quote_field and quote_missing:
            _reject(cell, f"цитата не найдена в закадре: {str(value)[:60]!r}")
        cells.append(cell)
    return cells


def slice_to_cells(
    project: Project, agent: str, slice_data: dict[str, Any], full_vo: str
) -> list[SceneDesignCell]:
    """Срез агента → список ячеек (ok + rejected). Неизвестный агент → []."""
    vo_norm = _norm(full_vo)
    cells: list[SceneDesignCell] = []

    if agent == "characters":
        for item in slice_data.get("characters") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or "").strip()
            if not key:
                continue
            part = _item_fields(
                project, agent, "character", key, item,
                ("имя", "внешность", "одежда", "характер", "правила"),
                quote_field=None, full_vo_norm=vo_norm,
            )
            if not _ID_RE["character"].match(key):
                for c in part:
                    _reject(c, f"id персонажа не по шаблону cNN: {key!r}")
            cells.extend(part)

    elif agent == "world":
        for item in slice_data.get("locations") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or "").strip()
            if not key:
                continue
            part = _item_fields(
                project, agent, "location", key, item,
                ("name", "описание", "зоны", "предметы", "освещение", "время",
                 "триггеры_закадра"),
                quote_field=None, full_vo_norm=vo_norm,
            )
            if not _ID_RE["location"].match(key):
                for c in part:
                    _reject(c, f"id локации не по шаблону locNN: {key!r}")
            cells.extend(part)

    elif agent == "style":
        for idx, item in enumerate(slice_data.get("style_arc") or [], start=1):
            if not isinstance(item, dict):
                continue
            key = f"stage_{idx:02d}"
            part = _item_fields(
                project, agent, "style_stage", key, item,
                ("scene_hint", "тип_сцены", "освещение", "палитра", "тон", "сдвиг"),
                quote_field="scene_hint", full_vo_norm=vo_norm,
            )
            cells.extend(part)

    elif agent == "action":
        for item in slice_data.get("scenes") or []:
            if not isinstance(item, dict):
                continue
            key = str(item.get("id_scene") or "").strip()
            if not key:
                continue
            part = _item_fields(
                project, agent, "scene", key, item,
                ("start_words", "end_words", "время_сек", "структура_сцены",
                 "тип_стыка", "переход_в_сцену", "цепь_действия", "смысл_сцены",
                 "мотив", "связь_с_прошлой", "крючок_в_следующую", "location",
                 "кто_в_кадре"),
                quote_field="start_words", full_vo_norm=vo_norm,
            )
            for c in part:
                if (
                    c.field == "end_words"
                    and c.status == "ok"
                    and _vo_offset(vo_norm, c.value) is None
                ):
                    _reject(c, f"цитата не найдена в закадре: {c.value[:60]!r}")
                if c.field == "структура_сцены" and c.value not in SCENE_STRUCTURES:
                    _reject(c, f"структура_сцены вне словаря: {c.value!r}")
                if c.field == "тип_стыка" and c.value not in JOINT_TYPES:
                    _reject(c, f"тип_стыка вне словаря: {c.value!r}")
                if c.field == "переход_в_сцену":
                    first = c.value.split()[0] if c.value else ""
                    if first not in TRANSITIONS:
                        _reject(c, f"переход_в_сцену вне словаря: {c.value!r}")
            cells.extend(part)

    elif agent == "camera":
        for idx, item in enumerate(slice_data.get("shot_plan") or [], start=1):
            if not isinstance(item, dict):
                continue
            key = f"shot_{idx:02d}"
            part = _item_fields(
                project, agent, "shot", key, item,
                ("id_scene", "phase_index", "beat", "цитата", "крупность",
                 "особенность_сцены", "композиция", "угол", "движение",
                 "переход", "тип_стыка", "набор", "мотив", "кто_в_кадре"),
                quote_field="цитата", full_vo_norm=vo_norm,
            )
            for c in part:
                if c.field == "особенность_сцены" and c.value not in SHOT_FEATURES:
                    _reject(c, f"особенность_сцены вне словаря: {c.value!r}")
                if c.field == "тип_стыка" and c.value not in JOINT_TYPES:
                    _reject(c, f"тип_стыка вне словаря: {c.value!r}")
            cells.extend(part)

    return cells


async def store_cells(
    session: AsyncSession,
    project: Project,
    agent: str,
    cells: list[SceneDesignCell],
    *,
    chunk_size: int = _CHUNK,
) -> dict[str, int]:
    """Перезаписать ячейки агента частями (короткие транзакции, коммит на чанк)."""
    await session.execute(
        delete(SceneDesignCell).where(
            SceneDesignCell.project_id == project.id,
            SceneDesignCell.agent == agent,
        )
    )
    await session.commit()
    stored = rejected = 0
    for i in range(0, len(cells), chunk_size):
        chunk = cells[i : i + chunk_size]
        session.add_all(chunk)
        await session.commit()
        stored += len(chunk)
        rejected += sum(1 for c in chunk if c.status == "rejected")
    if rejected:
        logger.warning(
            "[#{}] scene_design/{}: {} ячеек отклонено из {}",
            project.id, agent, rejected, stored,
        )
    return {"stored": stored, "rejected": rejected}


async def load_cells(
    session: AsyncSession, project: Project, *, only_ok: bool = True
) -> list[SceneDesignCell]:
    """Все ячейки проекта (по умолчанию только валидные)."""
    stmt = select(SceneDesignCell).where(SceneDesignCell.project_id == project.id)
    if only_ok:
        stmt = stmt.where(SceneDesignCell.status == "ok")
    stmt = stmt.order_by(SceneDesignCell.id)
    return list((await session.execute(stmt)).scalars().all())


async def wipe_cells(
    session: AsyncSession, project: Project, *, agent: str | None = None
) -> int:
    """Удалить staging-ячейки проекта (reset шага); ``agent`` — только одного."""
    stmt = delete(SceneDesignCell).where(SceneDesignCell.project_id == project.id)
    if agent:
        stmt = stmt.where(SceneDesignCell.agent == agent)
    res = await session.execute(stmt)
    return int(res.rowcount or 0)
