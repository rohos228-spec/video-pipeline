"""Ревью #352 п.9: удалённый ребёнок не получает слот при пересборке покрытия."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, Scene


@pytest.mark.asyncio
async def test_deleted_child_is_not_revived_by_coverage() -> None:
    from app.services.montage_coverage_ops import apply_coverage_scene_action

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    parent_uuid = "cc" * 12
    child_uuid = "dd" * 12
    async with factory() as session:
        project = Project(slug="cov-dead", topic="t", meta={})
        session.add(project)
        await session.flush()
        scene = Scene(project_id=project.id, sort_key=1.0, title="scene_kept", attrs={})
        session.add(scene)
        await session.flush()
        parent = Frame(
            project_id=project.id,
            number=3,
            uuid=parent_uuid,
            voiceover_text="Альфа у двери.",
            status="planned",
            sort_key=3.0,
            scene_id=scene.id,
            attrs={
                "camera_subdivide": {
                    "role": "vo_parent",
                    "parent_uuid": parent_uuid,
                    "shot_index": 1,
                },
                "кадры": [
                    {"id": "3-K1", "действие": "стоит", "закадр": "Альфа у двери."},
                    {"id": "3-K2", "действие": "смотрит", "закадр": "смотрит в щель"},
                ],
            },
        )
        child = Frame(
            project_id=project.id,
            number=4,
            uuid=child_uuid,
            voiceover_text="смотрит в щель",
            status="planned",
            sort_key=4.0,
            scene_id=scene.id,
            attrs={
                "активен": False,
                "удалён": True,
                "camera_subdivide": {
                    "role": "shot",
                    "parent_uuid": parent_uuid,
                    "shot_index": 2,
                },
            },
        )
        session.add(parent)
        session.add(child)
        await session.flush()
        await apply_coverage_scene_action(
            session,
            project,
            parent,
            [parent, child],
            "стоит у двери",
            kadry=[
                {"id": "3-K1", "действие": "стоит у двери", "закадр": "Альфа у двери.", "план": "СРЕДНИЙ"},
                {"id": "3-K2", "действие": "смотрит", "закадр": "смотрит в щель", "план": "КРУПНЫЙ"},
            ],
            lock_parent_plan=False,
        )
        await session.flush()
        fresh = (
            await session.execute(select(Frame).where(Frame.uuid == child_uuid))
        ).scalar_one()
        cs = (fresh.attrs or {}).get("camera_subdivide") or {}
        assert fresh.attrs.get("активен") is False
        assert cs.get("coverage_kind") != "child"
    await engine.dispose()
