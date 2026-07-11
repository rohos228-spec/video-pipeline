"""Дефолтный шаблон Workflow — сид на старте сервера."""

from __future__ import annotations

from datetime import datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import Workflow
from app.orchestrator.default_graph import LAYOUT_VERSION, default_graph as _default_graph


def default_workflow_needs_refresh(wf: Workflow) -> bool:
    """True если дефолтный граф устарел или в нём нет обязательных нод."""
    meta = dict(wf.meta or {})
    if meta.get("layout_version") != LAYOUT_VERSION:
        return True
    types = {str(n.get("type") or "") for n in (wf.nodes or [])}
    if "topic" not in types:
        return True
    # Миграция enrich_1..5 → excel_gpt (layout 5 → 6+).
    if any(t.startswith("enrich_") for t in types):
        return True
    if "excel_gpt" not in types and any("enrich" in t for t in types):
        return True
    return False


async def apply_default_graph(session: AsyncSession, wf: Workflow) -> bool:
    """Подменить nodes/edges дефолтного workflow на фабричный граф.

    Возвращает True если были изменения.
    """
    if not default_workflow_needs_refresh(wf):
        return False

    from app.services.workflow_run_sync import sync_runs_from_workflow

    nodes, edges = _default_graph()
    wf.nodes = nodes
    wf.edges = edges
    meta = dict(wf.meta or {})
    meta["layout_version"] = LAYOUT_VERSION
    wf.meta = meta
    wf.version = (wf.version or 1) + 1
    wf.updated_at = datetime.utcnow()
    await sync_runs_from_workflow(session, wf)
    logger.info(
        "default workflow #{} refreshed to layout_version={} ({} nodes)",
        wf.id,
        LAYOUT_VERSION,
        len(nodes),
    )
    return True


async def seed_default_workflow() -> None:
    """Создаёт или обновляет системный default Workflow."""
    nodes, edges = _default_graph()
    async with session_scope() as session:
        existing = (
            await session.execute(
                select(Workflow).where(Workflow.is_default == True)  # noqa: E712
            )
        ).scalar_one_or_none()

        if existing is None:
            wf = Workflow(
                name="Стандартный shorts-pipeline",
                description=(
                    "Полный 60–75 сек ролик: тема → план → сценарий → разбивка → "
                    "герои/предметы → enrich → image_prompts → images → "
                    "анимация → видео → аудио → сборка → публикация."
                ),
                nodes=nodes,
                edges=edges,
                is_default=True,
                version=1,
                meta={"layout_version": LAYOUT_VERSION},
            )
            session.add(wf)
            return

        await apply_default_graph(session, existing)
