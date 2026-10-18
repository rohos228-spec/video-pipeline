"""Ревью #352: apply_coverage_scene_action не выкидывает шот, которого нет во входящих кадрах."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, Scene


@pytest.mark.asyncio
async def test_apply_coverage_scene_action_keeps_frames_and_shot_ids() -> None:
    """apply_coverage_scene_action не удаляет кадры и не выкидывает id шота."""
    from app.services.montage_coverage_ops import apply_coverage_scene_action

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    parent_uuid = "aa" * 12
    child_uuid = "bb" * 12
    async with factory() as session:
        project = Project(slug="cov-keep", topic="t", meta={})
        session.add(project)
        await session.flush()
        scene = Scene(
            project_id=project.id,
            sort_key=1.0,
            title="scene_kept",
            attrs={"id_scene": "scene_kept"},
        )
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
                    {
                        "id": "3-K1",
                        "действие": "стоит",
                        "закадр": "Альфа у двери.",
                        "план": "ОБЩИЙ",
                    },
                    {
                        "id": "3-K9",
                        "действие": "хвост",
                        "закадр": "Хвост кадра.",
                    },
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
                "camera_subdivide": {
                    "role": "shot",
                    "parent_uuid": parent_uuid,
                    "shot_index": 2,
                }
            },
        )
        session.add(parent)
        session.add(child)
        await session.flush()
        parent_id = parent.id
        child_id = child.id
        scene_id = scene.id

        report = await apply_coverage_scene_action(
            session,
            project,
            parent,
            [parent, child],
            "стоит у двери",
            kadry=[
                {
                    "id": "3-K1",
                    "действие": "стоит у двери",
                    "закадр": "Альфа у двери.",
                    "план": "СРЕДНИЙ",
                }
            ],
            lock_parent_plan=False,
        )
        await session.flush()

        frames = list(
            (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project.id)
                    .order_by(Frame.number)
                )
            ).scalars()
        )
        scenes = list(
            (
                await session.execute(
                    select(Scene).where(Scene.project_id == project.id)
                )
            ).scalars()
        )
        assert [(fr.id, fr.uuid, fr.number) for fr in frames] == [
            (parent_id, parent_uuid, 3),
            (child_id, child_uuid, 4),
        ]
        assert [sc.id for sc in scenes] == [scene_id]
        assert scenes[0].title == "scene_kept"
        stored = (parent.attrs or {}).get("кадры") or []
        by_id = {item["id"]: item for item in stored if isinstance(item, dict)}
        assert "3-K1" in by_id
        assert by_id["3-K1"]["id"] == "3-K1"
        assert "3-K9" in by_id
        assert by_id["3-K9"]["id"] == "3-K9"
        assert by_id["3-K9"]["активен"] is False
        assert by_id["3-K9"]["закадр"] == "Хвост кадра."
        assert report["inserted_frames"] == 0
    await engine.dispose()
