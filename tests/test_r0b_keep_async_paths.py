"""Три пути, которые детекторы не прогоняли: кадры и сцены не стираются."""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, ProjectStatus, Scene
from app.services import db_v2
from app.settings import settings
from app.web.api import create_app
from app.web.deps import get_session


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


@pytest_asyncio.fixture
async def apply_ops_client(tmp_path, monkeypatch):
    db_file = tmp_path / "r0b-apply.db"
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "sqlite_path", db_file)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app()

    async def _gen():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_session] = _gen
    async with factory() as session:
        project = Project(
            topic="r0b",
            slug="r0b-apply",
            status=ProjectStatus.new,
            meta={
                "scene_registry": [
                    {
                        "id_scene": "scene_kept",
                        "start_words": "Альфа",
                        "end_words": "один",
                        "shots": [{"id": "shot-a"}],
                    },
                    {
                        "id_scene": "scene_tail",
                        "start_words": "Бета",
                        "end_words": "два",
                        "shots": [{"id": "shot-b"}],
                    },
                ]
            },
        )
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
        session.add(
            Frame(
                project_id=project.id,
                number=1,
                uuid="frame-u1",
                voiceover_text="Альфа один.",
                status="planned",
                sort_key=1.0,
                scene_id=scene.id,
                attrs={
                    "кадры": [
                        {"id": "1-K1", "действие": "первый"},
                        {"id": "1-K2", "действие": "второй"},
                    ]
                },
            )
        )
        session.add(
            Frame(
                project_id=project.id,
                number=2,
                uuid="frame-u2",
                voiceover_text="Бета два.",
                status="planned",
                sort_key=2.0,
                scene_id=scene.id,
                attrs={"кадры": [{"id": "2-K1", "действие": "третий"}]},
            )
        )
        await session.commit()
        project_id = project.id
        scene_id = scene.id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, project_id, scene_id, factory
    app.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_db_browser_apply_ops_does_not_delete_frames(apply_ops_client) -> None:
    """Правка одного кадра не удаляет второй и не меняет uuid."""
    client, project_id, scene_id, factory = apply_ops_client
    response = await client.post(
        f"/api/db/projects/{project_id}/apply-ops",
        json={
            "ops": [
                {
                    "target": "frame",
                    "frame_uuid": "frame-u1",
                    "fields": {"закадр": "Альфа один, правка."},
                }
            ]
        },
    )
    assert response.status_code == 200, response.text
    async with factory() as session:
        frames = list(
            (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project_id)
                    .order_by(Frame.number)
                )
            ).scalars()
        )
        scenes = list(
            (
                await session.execute(
                    select(Scene).where(Scene.project_id == project_id)
                )
            ).scalars()
        )
        assert [(fr.uuid, fr.number) for fr in frames] == [
            ("frame-u1", 1),
            ("frame-u2", 2),
        ]
        assert frames[0].voiceover_text == "Альфа один, правка."
        assert frames[1].voiceover_text == "Бета два."
        assert [item["id"] for item in frames[0].attrs["кадры"]] == ["1-K1", "1-K2"]
        assert [item["id"] for item in frames[1].attrs["кадры"]] == ["2-K1"]
        assert [sc.id for sc in scenes] == [scene_id]
        assert scenes[0].title == "scene_kept"


@pytest.mark.asyncio
async def test_db_browser_apply_ops_keeps_partial_kadry_and_scenes(
    apply_ops_client,
) -> None:
    """Короткий список кадров и сцен не выкидывает старые id."""
    client, project_id, scene_id, factory = apply_ops_client
    response = await client.post(
        f"/api/db/projects/{project_id}/apply-ops",
        json={
            "ops": [
                {
                    "target": "frame",
                    "frame_uuid": "frame-u1",
                    "fields": {
                        "кадры": [{"id": "1-K1", "действие": "первый, новый"}],
                    },
                }
            ],
            "scenes": [
                {
                    "id_scene": "scene_kept",
                    "start_words": "Альфа",
                    "end_words": "один",
                    "shots": [{"id": "shot-a"}],
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    async with factory() as session:
        project = await session.get(Project, project_id)
        frames = list(
            (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project_id)
                    .order_by(Frame.number)
                )
            ).scalars()
        )
        scenes = list(
            (
                await session.execute(
                    select(Scene).where(Scene.project_id == project_id)
                )
            ).scalars()
        )
        assert [(fr.uuid, fr.number) for fr in frames] == [
            ("frame-u1", 1),
            ("frame-u2", 2),
        ]
        assert [sc.id for sc in scenes] == [scene_id]
        stored = frames[0].attrs["кадры"]
        by_id = {item["id"]: item for item in stored}
        assert by_id["1-K1"]["id"] == "1-K1"
        assert by_id["1-K1"].get("активен") is not False
        assert by_id["1-K2"]["id"] == "1-K2"
        assert by_id["1-K2"]["активен"] is False
        assert [item["id"] for item in frames[1].attrs["кадры"]] == ["2-K1"]
        registry = (project.meta or {}).get("scene_registry") or []
        reg_ids = [item.get("id_scene") for item in registry]
        assert "scene_kept" in reg_ids
        assert "scene_tail" in reg_ids
        tail = next(item for item in registry if item.get("id_scene") == "scene_tail")
        assert tail.get("активен") is False
