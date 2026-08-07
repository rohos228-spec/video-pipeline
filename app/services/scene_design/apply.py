"""Запись результата scene_design через существующий контракт apply-ops."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project


async def apply_scene_design(
    session: AsyncSession,
    project: Project,
    payload: dict[str, Any],
) -> dict:
    """characters → Entity, scenes → meta.scene_registry, ops → поля кадров.

    Полностью переиспользует ``db_apply.apply_ops`` — тот же контракт,
    что у scene_grammar_unified_agent (excel_gpt legacy-путь).
    """
    from app.services import db_apply

    return await db_apply.apply_ops(
        session,
        project,
        list(payload.get("ops") or []),
        characters=list(payload.get("characters") or []) or None,
        scenes=list(payload.get("scenes") or []) or None,
        export_xlsx=True,
    )
