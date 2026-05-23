"""Дефолтный шаблон Workflow — сид на старте сервера."""

from __future__ import annotations

from sqlalchemy import select

from app.db import session_scope
from app.models import Workflow
from app.orchestrator.default_graph import LAYOUT_VERSION, default_graph as _default_graph


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
                    "Полный 60–75 сек ролик: план → сценарий → разбивка → "
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

        meta = dict(existing.meta or {})
        if meta.get("layout_version") != LAYOUT_VERSION:
            existing.nodes = nodes
            existing.edges = edges
            meta["layout_version"] = LAYOUT_VERSION
            existing.meta = meta
            existing.version = (existing.version or 1) + 1
