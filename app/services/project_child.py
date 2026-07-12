"""Ручное создание дочернего проекта из родителя (копия данных + закадровый текст)."""

from __future__ import annotations

import copy
import shutil
from typing import Any

from loguru import logger
from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.services.mass_factory import (
    COPY_PROJECT_FIELDS,
    STRIP_META_KEYS,
    ensure_child_workflow_from_parent,
    is_mass_factory_child,
    list_mass_children,
)
from app.storage import ProjectSheet

MANUAL_CHILD_EXTRA_FIELDS = (
    "general_plan",
    "hero_description",
    "script_text",
)


def _child_initial_status(parent: Project) -> ProjectStatus:
    if (parent.script_text or "").strip():
        return ProjectStatus.script_ready
    if (parent.general_plan or "").strip():
        return ProjectStatus.plan_ready
    return ProjectStatus.new


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


def _copy_parent_data_dir(parent: Project, child: Project) -> None:
    src = parent.data_dir
    dst = child.data_dir
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    if src.is_dir():
        shutil.copytree(
            src,
            dst,
            ignore=shutil.ignore_patterns("tmp_gpt", "__pycache__"),
        )
    else:
        dst.mkdir(parents=True, exist_ok=True)
    xlsx = dst / "project.xlsx"
    if xlsx.is_file():
        try:
            sheet = ProjectSheet(file_path=xlsx)
            sheet.write_general(
                topic=child.topic,
                slug=child.slug,
                hero_mode=child.hero_mode,
                status=child.status.value,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[#{}] child data_dir: xlsx patch failed: {}", child.id, exc)
    if (parent.script_text or "").strip():
        vo = dst / "voiceover.txt"
        vo.parent.mkdir(parents=True, exist_ok=True)
        vo.write_text((parent.script_text or "").strip(), encoding="utf-8")


async def create_child_from_parent(
    session: AsyncSession,
    parent: Project,
    *,
    slugify,
) -> Project:
    if is_mass_factory_child(parent):
        raise ValueError("дочерний проект нельзя клонировать — выберите родительский")
    existing = await list_mass_children(session, parent.id)
    child_index = len(existing) + 1
    topic_base = f"{parent.topic.strip()} · доч. {child_index}"
    slug = await _unique_slug(session, topic_base, slugify)

    kwargs: dict[str, Any] = {}
    for field in (*COPY_PROJECT_FIELDS, *MANUAL_CHILD_EXTRA_FIELDS):
        kwargs[field] = getattr(parent, field)
    kwargs["auto_mode"] = parent.auto_mode

    child = Project(
        slug=slug,
        topic=topic_base,
        status=_child_initial_status(parent),
        meta=build_manual_child_meta(
            dict(parent.meta or {}),
            parent_id=parent.id,
            child_index=child_index,
        ),
        **kwargs,
    )
    session.add(child)
    await session.flush()
    _copy_parent_data_dir(parent, child)
    await ensure_child_workflow_from_parent(session, parent.id, child.id)
    logger.info(
        "project_child: #{} ← parent #{} (script={} chars)",
        child.id,
        parent.id,
        len(child.script_text or ""),
    )
    return child


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
