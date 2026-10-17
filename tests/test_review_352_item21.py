"""№21: промты_детей доходят до attrs кадра через apply-ops."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services import db_v2
from app.services.db_apply import apply_ops


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_apply_ops_writes_child_prompts_alias(session: AsyncSession) -> None:
    project = Project(slug="item21", topic="t", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    fr = Frame(
        project_id=project.id,
        number=1,
        uuid="child-prompts-frame",
        voiceover_text="закадр",
        attrs={},
    )
    session.add(fr)
    await session.commit()
    children = ["крупный план рук", "общий план кабинета"]
    await apply_ops(
        session,
        project,
        [
            {
                "frame_uuid": fr.uuid,
                "fields": {
                    "промт_картинки": "кадр родителя",
                    "промты_детей": children,
                },
            }
        ],
        export_xlsx=False,
        node_kind="excel_gpt_prompts",
    )
    await session.refresh(fr)
    assert fr.image_prompt == "кадр родителя"
    assert fr.attrs["child_prompts"] == children
    assert fr.attrs["промты_детей"] == children
