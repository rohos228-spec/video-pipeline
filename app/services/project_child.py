"""Ручное создание дочернего проекта из родителя.

От родителя наследуются только настройки генерации, промты и тексты для GPT.
Не копируются: закадровый текст, Excel с данными, результаты генерации.
"""

from __future__ import annotations

import copy
from typing import Any

from loguru import logger
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.services.mass_factory import (
    COPY_META_KEYS,
    COPY_PROJECT_FIELDS,
    STRIP_META_KEYS,
    ensure_child_workflow_from_parent,
    init_child_data_dir,
    is_mass_factory_child,
    list_mass_children,
)

# Meta с настройками UI/промтов (не прогресс генерации).
_MANUAL_CHILD_META_KEYS = frozenset(
    {
        *COPY_META_KEYS,
        "canvas_graph",
        "excel_gpt_nodes",
        "node_step_params",
        "sidebar_folder_id",
    }
)


def build_manual_child_meta(
    parent_meta: dict[str, Any],
    *,
    parent_id: int,
    child_index: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in parent_meta.items():
        if key in STRIP_META_KEYS:
            continue
        if key in _MANUAL_CHILD_META_KEYS or key.startswith("prompt_"):
            out[key] = copy.deepcopy(val)
    out["mass_parent_id"] = parent_id
    out["mass_lane_position"] = child_index
    out["project_child_manual"] = True
    return out


async def _unique_slug(session: AsyncSession, base: str, slugify) -> str:
    slug = slugify(base)
    candidate = slug
    suffix = 2
    while (
        await session.execute(select(Project).where(Project.slug == candidate))
    ).scalar_one_or_none() is not None:
        candidate = f"{slug}-{suffix}"
        suffix += 1
    return candidate


async def create_child_from_parent(
    session: AsyncSession,
    parent: Project,
    *,
    slugify,
) -> Project:
    """Создаёт ребёнка: настройки/промты/gpt_text + workflow (без контента)."""
    if is_mass_factory_child(parent):
        raise ValueError("дочерний проект нельзя клонировать — выберите родительский")
    existing = await list_mass_children(session, parent.id)
    child_index = len(existing) + 1
    topic_base = f"{parent.topic.strip()} · доч. {child_index}"
    slug = await _unique_slug(session, topic_base, slugify)

    kwargs: dict[str, Any] = {}
    for field in COPY_PROJECT_FIELDS:
        if field == "auto_mode":
            continue
        kwargs[field] = copy.deepcopy(getattr(parent, field))
    # Не тянем auto_mode и контент (script/plan/hero_description).
    kwargs["auto_mode"] = False

    child = Project(
        slug=slug,
        topic=topic_base,
        status=ProjectStatus.new,
        general_plan=None,
        hero_description=None,
        script_text=None,
        meta=build_manual_child_meta(
            dict(parent.meta or {}),
            parent_id=parent.id,
            child_index=child_index,
        ),
        **kwargs,
    )
    session.add(child)
    await session.flush()
    await ensure_child_workflow_from_parent(session, parent.id, child.id)
    logger.info(
        "project_child: #{} ← parent #{} (settings/prompts only, status=new)",
        child.id,
        parent.id,
    )
    return child


async def finalize_child_data_dir(_parent: Project, child: Project) -> None:
    """После commit: пустой data_dir и свежий template.xlsx (не копия родителя)."""
    await init_child_data_dir(child)


async def count_children(session: AsyncSession, parent_id: int) -> int:
    parent_expr = cast(func.json_extract(Project.meta, "$.mass_parent_id"), Integer)
    return int(
        (
            await session.execute(
                select(func.count()).select_from(Project).where(parent_expr == parent_id)
            )
        ).scalar_one()
        or 0
    )
