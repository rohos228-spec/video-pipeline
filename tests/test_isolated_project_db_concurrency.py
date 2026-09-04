"""Stress-тест параллельной работы с изолированными базами данных проектов.

Проверяет:
1. Два проекта могут параллельно и непрерывно писать в свои базы project.db без взаимных блокировок.
2. Отсутствие ошибок sqlite3.OperationalError: database is locked между разными проектами.
3. Полную изоляцию данных: кадры Проекта 1 не попадают в Проект 2.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import Frame, Project, ProjectStatus
from app.project_db import (
    close_all_project_engines,
    get_project_sessionmaker,
    init_project_db,
    project_session_scope,
)


@pytest.mark.asyncio
async def test_concurrent_project_db_writes(tmp_path: Path):
    dir_p1 = tmp_path / "proj_1"
    dir_p2 = tmp_path / "proj_2"
    dir_p1.mkdir()
    dir_p2.mkdir()

    p1 = Project(id=101, slug="proj-1", title="Project 1", topic="Topic 1", status=ProjectStatus.new)
    p2 = Project(id=102, slug="proj-2", title="Project 2", topic="Topic 2", status=ProjectStatus.new)

    await init_project_db(dir_p1, project=p1)
    await init_project_db(dir_p2, project=p2)

    # Параллельные писатели для Проекта 1
    async def writer_p1(task_id: int):
        for i in range(15):
            async with project_session_scope(dir_p1) as session:
                frame = Frame(
                    project_id=101,
                    uuid=f"p1_{task_id}_{i}",
                    number=task_id * 100 + i,
                    voiceover_text=f"P1 voiceover task {task_id} iter {i}",
                    attrs={"task": task_id, "iter": i},
                )
                session.add(frame)
            await asyncio.sleep(0.01)

    # Параллельные писатели для Проекта 2
    async def writer_p2(task_id: int):
        for i in range(15):
            async with project_session_scope(dir_p2) as session:
                frame = Frame(
                    project_id=102,
                    uuid=f"p2_{task_id}_{i}",
                    number=task_id * 100 + i,
                    voiceover_text=f"P2 voiceover task {task_id} iter {i}",
                    attrs={"task": task_id, "iter": i},
                )
                session.add(frame)
            await asyncio.sleep(0.01)

    # Запускаем 10 писателей: 5 для проекта 1 и 5 для проекта 2 одновременно
    tasks = []
    for t in range(5):
        tasks.append(asyncio.create_task(writer_p1(t)))
        tasks.append(asyncio.create_task(writer_p2(t)))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Проверяем, что ни одна задача не упала с ошибкой
    for res in results:
        if isinstance(res, Exception):
            raise res

    # Проверяем целостность данных в P1
    sm1 = await get_project_sessionmaker(dir_p1)
    async with sm1() as s1:
        count_p1 = (await s1.execute(select(func.count(Frame.id)))).scalar_one()
        assert count_p1 == 5 * 15, f"Expected 75 frames in P1, got {count_p1}"
        p1_frames = (await s1.execute(select(Frame))).scalars().all()
        for f in p1_frames:
            assert f.project_id == 101
            assert f.uuid.startswith("p1_")

    # Проверяем целостность данных в P2
    sm2 = await get_project_sessionmaker(dir_p2)
    async with sm2() as s2:
        count_p2 = (await s2.execute(select(func.count(Frame.id)))).scalar_one()
        assert count_p2 == 5 * 15, f"Expected 75 frames in P2, got {count_p2}"
        p2_frames = (await s2.execute(select(Frame))).scalars().all()
        for f in p2_frames:
            assert f.project_id == 102
            assert f.uuid.startswith("p2_")

    await close_all_project_engines()


@pytest.mark.asyncio
async def test_concurrent_fastapi_endpoints_isolated_dbs(tmp_path: Path):
    """Тест параллельных REST-запросов к FastAPI эндпоинтам разных проектов с project.db."""
    from httpx import ASGITransport, AsyncClient
    from openpyxl import Workbook
    from app.web.api import create_app
    from app.project_db import register_project_data_dir, unregister_project_data_dir

    dir_p1 = tmp_path / "fastapi_p1"
    dir_p2 = tmp_path / "fastapi_p2"
    dir_p1.mkdir()
    dir_p2.mkdir()

    p1 = Project(id=201, slug="api-p1", title="API Project 1", topic="Topic 1", status=ProjectStatus.new)
    p2 = Project(id=202, slug="api-p2", title="API Project 2", topic="Topic 2", status=ProjectStatus.new)

    await init_project_db(dir_p1, project=p1)
    await init_project_db(dir_p2, project=p2)

    register_project_data_dir(201, dir_p1)
    register_project_data_dir(202, dir_p2)

    # Добавляем начальные кадры в каждый проект
    async with project_session_scope(dir_p1) as s1:
        s1.add(Frame(project_id=201, number=1, uuid="p1_init_1", voiceover_text="P1 init text"))
    async with project_session_scope(dir_p2) as s2:
        s2.add(Frame(project_id=202, number=1, uuid="p2_init_1", voiceover_text="P2 init text"))

    app = create_app()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Параллельные запросы чтения графа
        res_g1, res_g2 = await asyncio.gather(
            client.get("/api/db/projects/201/graph"),
            client.get("/api/db/projects/202/graph"),
        )
        assert res_g1.status_code == 200
        assert res_g2.status_code == 200
        assert res_g1.json()["project"]["id"] == 201
        assert res_g2.json()["project"]["id"] == 202

        # 2. Параллельные вставки кадров через REST
        res_i1, res_i2 = await asyncio.gather(
            client.post("/api/db/projects/201/frames/insert", json={}),
            client.post("/api/db/projects/202/frames/insert", json={}),
        )
        assert res_i1.status_code == 201
        assert res_i2.status_code == 201

        # 3. Параллельные apply-ops в разные базы
        op_p1 = {"ops": [{"frame_uuid": "p1_init_1", "fields": {"voiceover_text": "P1 updated by apply-ops"}}]}
        op_p2 = {"ops": [{"frame_uuid": "p2_init_1", "fields": {"voiceover_text": "P2 updated by apply-ops"}}]}
        res_op1, res_op2 = await asyncio.gather(
            client.post("/api/db/projects/201/apply-ops", json=op_p1),
            client.post("/api/db/projects/202/apply-ops", json=op_p2),
        )
        assert res_op1.status_code == 200, res_op1.text
        assert res_op2.status_code == 200, res_op2.text

        # 4. Проверка через /api/projects/{id}/frames
        res_f1, res_f2 = await asyncio.gather(
            client.get("/api/projects/201/frames"),
            client.get("/api/projects/202/frames"),
        )
        assert res_f1.status_code == 200
        assert res_f2.status_code == 200
        frames_1 = res_f1.json()
        frames_2 = res_f2.json()

        assert len(frames_1) == 2  # init + inserted
        assert len(frames_2) == 2  # init + inserted
        assert frames_1[0]["voiceover_text"] == "P1 updated by apply-ops"
        assert frames_2[0]["voiceover_text"] == "P2 updated by apply-ops"

    unregister_project_data_dir(201)
    unregister_project_data_dir(202)
    await close_all_project_engines()


@pytest.mark.asyncio
async def test_legacy_project_migration(tmp_path: Path):
    """Тест миграции проекта из master базы данных в project.db."""
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.models import Base, Scene, Artifact, ArtifactKind, Entity
    from app.project_db import migrate_single_project_data

    master_db_path = tmp_path / "master.db"
    master_engine = create_async_engine(f"sqlite+aiosqlite:///{master_db_path.as_posix()}")
    async with master_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    MasterSession = async_sessionmaker(master_engine, expire_on_commit=False)

    proj_dir = tmp_path / "legacy_proj"
    proj_dir.mkdir()

    # Заполняем мастер-базу данными старого проекта
    async with MasterSession() as m_sess:
        p = Project(
            id=555,
            slug="legacy-555",
            title="Legacy Project",
            topic="History",
            status=ProjectStatus.frames_ready,
        )
        m_sess.add(p)
        await m_sess.flush()

        sc = Scene(id=10, project_id=555, sort_key=1, title="Intro Scene")
        m_sess.add(sc)

        fr = Frame(
            id=100,
            project_id=555,
            number=1,
            scene_id=10,
            voiceover_text="Voiceover in legacy",
            uuid="fr-leg-1",
        )
        m_sess.add(fr)

        art = Artifact(
            id=200,
            project_id=555,
            frame_id=100,
            kind=ArtifactKind.scene_image,
            uuid="art-leg-1",
            path="path/to/img.png",
        )
        m_sess.add(art)

        ent = Entity(
            id=300,
            project_id=555,
            type="character",
            code="hero_1",
            name="Arthur",
        )
        m_sess.add(ent)

        await m_sess.commit()

    # Вызываем миграцию
    async with MasterSession() as m_sess:
        p_loaded = await m_sess.get(Project, 555)
        counts = await migrate_single_project_data(
            m_sess, p_loaded, data_dir=proj_dir, create_backup=True
        )

    assert counts["frames"] == 1
    assert counts["scenes"] == 1
    assert counts["artifacts"] == 1
    assert counts["entities"] == 1

    # Проверяем, что в project.db всё перенеслось точно
    sm = await get_project_sessionmaker(proj_dir)
    async with sm() as p_sess:
        p_migrated = await p_sess.get(Project, 555)
        assert p_migrated is not None
        assert p_migrated.slug == "legacy-555"
        assert p_migrated.status == ProjectStatus.frames_ready

        fr_migrated = await p_sess.get(Frame, 100)
        assert fr_migrated is not None
        assert fr_migrated.voiceover_text == "Voiceover in legacy"
        assert fr_migrated.uuid == "fr-leg-1"

        art_migrated = await p_sess.get(Artifact, 200)
        assert art_migrated is not None
        assert art_migrated.uuid == "art-leg-1"

        ent_migrated = await p_sess.get(Entity, 300)
        assert ent_migrated is not None
        assert ent_migrated.name == "Arthur"

    # Проверяем создание бэкапа при повторной миграции
    async with MasterSession() as m_sess:
        p_loaded = await m_sess.get(Project, 555)
        await migrate_single_project_data(
            m_sess, p_loaded, data_dir=proj_dir, create_backup=True
        )

    bak_path = proj_dir / "project.db.bak"
    assert bak_path.exists(), "project.db.bak must be created on re-migration"

    await master_engine.dispose()
    await close_all_project_engines()
