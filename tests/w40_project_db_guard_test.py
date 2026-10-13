"""Фаза 2 (S-3, S-1, D-9): плановый бэкап project.db, миграция схемы существующих project.db, коллизии id.

Все базы временные и лежат вне репозитория (фикстура outside_tmp); боевые data/state.db и data/videos не используются.
"""
from __future__ import annotations

import asyncio
import contextlib
import sqlite3
from pathlib import Path

import pytest

from app.services import project_db_guard as g
from app.settings import settings
from app.services import schema_migrations as sm

pytestmark = pytest.mark.asyncio


def _make_state(path: Path, slugs: list[str]) -> None:
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE projects (id INTEGER PRIMARY KEY, slug TEXT, batch_id INTEGER, batch_slug TEXT)")
        for i, slug in enumerate(slugs, 1):
            db.execute("INSERT INTO projects VALUES (?,?,NULL,NULL)", (i, slug))
        db.commit()


def _pdir(root: Path, slug: str) -> Path:
    return root / "videos" / slug


async def _own_master(monkeypatch, path: Path):
    """Свой master во временной папке. Глобальный app.db.engine смотрит на боевой data/state.db - его не трогаем."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.db as app_db
    from app.models import Base

    eng = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, expire_on_commit=False)
    monkeypatch.setattr(app_db, "engine", eng)
    monkeypatch.setattr(app_db, "SessionLocal", maker)
    return eng, maker


def _make_project_db(path: Path, rows: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE frames (id INTEGER PRIMARY KEY, project_id INTEGER, voiceover_text TEXT)")
        for i in range(1, rows + 1):
            db.execute("INSERT INTO frames VALUES (?,?,?)", (i, 7, f"t{i}"))
        db.commit()


# --- S-3 --------------------------------------------------------------------------------------------

async def test_backup_targets_list_state_tracker_and_every_project_db(outside_tmp):
    d1, d2 = _pdir(outside_tmp, "a"), _pdir(outside_tmp, "b")
    _make_project_db(d1 / "project.db")
    _make_project_db(d2 / "project.db")  # у проекта c файла project.db нет
    state = outside_tmp / "state.db"
    _make_state(state, ["a", "b", "c"])
    targets = g.backup_targets(state, root=outside_tmp, data_root=outside_tmp)
    assert targets[0] == state and targets[1] == outside_tmp / "api-tracker" / "tracker.db"
    assert set(targets[2:]) == {d1 / "project.db", d2 / "project.db"}


async def test_scheduled_pass_copies_and_verifies_each_project_db(outside_tmp):
    d1 = _pdir(outside_tmp, "a")
    _make_project_db(d1 / "project.db", rows=3)
    state = outside_tmp / "state.db"
    _make_state(state, ["a"])
    results = sm.run_scheduled_backups(g.backup_targets(state, root=outside_tmp, data_root=outside_tmp), interval_h=6.0)
    proj = next(r for r in results if Path(r["db"]).name == "project.db")
    assert proj["status"] == "backed_up" and proj["verified"] is True
    copy = Path(proj["path"])
    assert copy.parent == d1 / "backups" and copy.is_file()
    with contextlib.closing(sqlite3.connect(copy)) as db:
        assert db.execute("SELECT count(*) FROM frames").fetchone()[0] == 3
    # трекера нет: пропущен, не падение
    assert any(r["status"] == "missing" for r in results)


async def test_backup_task_runs_on_a_schedule_and_stops(outside_tmp):
    d1 = _pdir(outside_tmp, "a")
    _make_project_db(d1 / "project.db")
    state = outside_tmp / "state.db"
    _make_state(state, ["a"])
    stop = asyncio.Event()
    task = g.start_scheduled_backups(state, interval_h=6.0, check_every_s=0.05, stop=stop, data_root=outside_tmp)
    assert task is not None
    for _ in range(100):
        if sm.backup_files(d1 / "backups", "project", "scheduled"):
            break
        await asyncio.sleep(0.1)
    assert sm.backup_files(d1 / "backups", "project", "scheduled"), "плановая копия project.db не появилась"
    # новый проект подхватывается без перезапуска
    d2 = _pdir(outside_tmp, "b")
    _make_project_db(d2 / "project.db")
    with contextlib.closing(sqlite3.connect(state)) as db:
        db.execute("INSERT INTO projects VALUES (2,'b',NULL,NULL)")
        db.commit()
    for _ in range(100):
        if sm.backup_files(d2 / "backups", "project", "scheduled"):
            break
        await asyncio.sleep(0.1)
    assert sm.backup_files(d2 / "backups", "project", "scheduled")
    stop.set()
    await asyncio.wait_for(task, timeout=10)


async def test_backup_task_can_be_switched_off(monkeypatch, outside_tmp):
    monkeypatch.setenv(g.BACKUP_ENV, "0")
    assert g.start_scheduled_backups(outside_tmp / "state.db") is None


async def test_remigration_makes_a_verified_online_copy_even_without_the_flag(outside_tmp):
    """migrate_single_project_data над существующим project.db: копия обязательна и не зависит от create_backup."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, Project, ProjectStatus
    from app.project_db import close_all_project_engines, migrate_single_project_data

    master = create_async_engine(f"sqlite+aiosqlite:///{(outside_tmp / 'master.db').as_posix()}")
    async with master.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Master = async_sessionmaker(master, expire_on_commit=False)
    proj_dir = outside_tmp / "proj"
    proj_dir.mkdir()
    async with Master() as s:
        s.add(Project(id=9, slug="p9", title="t", topic="x", status=ProjectStatus.new))
        await s.commit()
    async with Master() as s:
        p = await s.get(Project, 9)
        await migrate_single_project_data(s, p, data_dir=proj_dir, create_backup=False)  # первая: файла ещё нет
    assert not sm.backup_files(proj_dir / "backups", "project", "pre-master-sync")
    async with Master() as s:
        p = await s.get(Project, 9)
        await migrate_single_project_data(s, p, data_dir=proj_dir, create_backup=False)  # вторая: данные есть
    made = sm.backup_files(proj_dir / "backups", "project", "pre-master-sync")
    assert len(made) == 1
    assert sm.verify_backup(made[0])["ok"] is True
    assert (proj_dir / "project.db.bak").is_file()
    await close_all_project_engines()
    await master.dispose()


# --- S-1 --------------------------------------------------------------------------------------------

async def test_existing_old_project_db_gets_projects_columns_and_a_copy(outside_tmp, monkeypatch):
    """Старый project.db без колонок 0009 получает их при старте; перед миграцией делается копия."""
    from app.models import Project, ProjectStatus
    from app.project_db import close_all_project_engines, resolve_project_db_path

    master_eng, Master = await _own_master(monkeypatch, outside_tmp / "master_s1.db")
    monkeypatch.setattr(settings, "data_dir", outside_tmp / "data")
    proj_dir = Path(settings.data_dir) / "videos" / "old"
    proj_dir.mkdir(parents=True)
    # старый project.db: таблица projects без позднее добавленных колонок
    old_cols = ("id INTEGER PRIMARY KEY, slug TEXT, title TEXT, topic TEXT, status TEXT, "
                "created_at TEXT, updated_at TEXT")
    with contextlib.closing(sqlite3.connect(resolve_project_db_path(proj_dir))) as db:
        db.execute(f"CREATE TABLE projects ({old_cols})")
        db.execute("INSERT INTO projects (id, slug, title, topic, status) VALUES (4,'old','t','x','new')")
        db.commit()

    async with Master() as s:
        s.add(Project(id=4, slug="old", title="t", topic="x", status=ProjectStatus.new))
        await s.commit()

    report = await g.migrate_existing_project_dbs()
    assert report["checked"] == 1 and report["failed"] == []
    with contextlib.closing(sqlite3.connect(resolve_project_db_path(proj_dir))) as db:
        cols = {r[1] for r in db.execute("PRAGMA table_info(projects)")}
        assert {c for c, _ in sm.PROJECTS_COLUMNS} <= cols
        assert "0009_projects_columns" in {r[0] for r in db.execute("SELECT id FROM schema_migrations")}
    assert sm.backup_files(proj_dir / "backups", "project", "pre-migration"), "копии перед миграцией нет"
    # повторный старт ничего не меняет и не копирует заново
    again = await g.migrate_existing_project_dbs()
    assert again["failed"] == []
    assert len(sm.backup_files(proj_dir / "backups", "project", "pre-migration")) == 1
    await close_all_project_engines()
    await master_eng.dispose()


async def test_migration_scope_of_0009_includes_project():
    m = {x.id: x for x in sm.MIGRATIONS}["0009_projects_columns"]
    assert sm.SCOPE_PROJECT in m.scopes and sm.SCOPE_MASTER in m.scopes


# --- D-9 --------------------------------------------------------------------------------------------

def _two_dbs(tmp: Path, master_rows: list[tuple], project_rows: list[tuple]) -> tuple[Path, Path]:
    master, proj = tmp / "m.db", tmp / "p.db"
    for path, rows in ((master, master_rows), (proj, project_rows)):
        with contextlib.closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE frames (id INTEGER PRIMARY KEY, project_id INTEGER)")
            db.execute("CREATE TABLE scenes (id INTEGER PRIMARY KEY, project_id INTEGER)")
            db.executemany("INSERT INTO frames VALUES (?,?)", rows)
            db.commit()
    return master, proj


async def test_same_id_same_project_is_not_a_collision(outside_tmp):
    master, proj = _two_dbs(outside_tmp, [(1, 7), (2, 7)], [(1, 7), (2, 7), (3, 7)])
    assert g.find_id_collisions(master, proj, 7) == []


async def test_same_id_different_project_is_reported_and_never_renumbered(outside_tmp):
    master, proj = _two_dbs(outside_tmp, [(1, 7), (5, 8)], [(1, 7), (5, 7)])
    found = g.find_id_collisions(master, proj, 7)
    assert found == [{"table": "frames", "id": 5, "master_project_id": 8, "project_db_project_id": 7}]
    # базы только прочитаны
    with contextlib.closing(sqlite3.connect(proj)) as db:
        assert db.execute("SELECT id, project_id FROM frames ORDER BY id").fetchall() == [(1, 7), (5, 7)]


async def test_open_check_logs_error_and_registers(outside_tmp, monkeypatch):
    from loguru import logger

    master_eng, _ = await _own_master(monkeypatch, outside_tmp / "master_d9.db")
    master_path = outside_tmp / "master_d9.db"
    with contextlib.closing(sqlite3.connect(master_path)) as db:
        db.execute("PRAGMA foreign_keys=OFF")
        db.execute("INSERT INTO frames (id, project_id, number, voiceover_text, status, attrs, created_at, updated_at) "
                   "VALUES (5, 8, 1, 'v', 'planned', '{}', '2026-01-01', '2026-01-01')")
        db.commit()
    proj_dir = outside_tmp / "pr"
    proj_dir.mkdir()
    with contextlib.closing(sqlite3.connect(proj_dir / "project.db")) as db:
        db.execute("CREATE TABLE frames (id INTEGER PRIMARY KEY, project_id INTEGER)")
        db.execute("CREATE TABLE scenes (id INTEGER PRIMARY KEY, project_id INTEGER)")
        db.execute("INSERT INTO frames VALUES (5, 7)")
        db.commit()
    seen: list[str] = []
    sink = logger.add(lambda m: seen.append(str(m)), level="ERROR")
    try:
        found = await g.check_project_id_collisions(7, proj_dir, force=True)
    finally:
        logger.remove(sink)
    assert [(f["table"], f["id"]) for f in found] == [("frames", 5)]
    assert g.collisions_for(7) == found
    assert any("ID COLLISION" in m for m in seen)
    await master_eng.dispose()


async def test_resolver_refuses_instead_of_silently_taking_the_other_projects_frame(outside_tmp, monkeypatch):
    """master знает кадр 5 как кадр проекта 8, проектная база считает его кадром проекта 7: отказ, а не ответ 8."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app import project_db
    from app.models import Base, Frame, Project, ProjectStatus

    async def make(path: Path, owner: int):
        eng = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        S = async_sessionmaker(eng, expire_on_commit=False)
        async with S() as s:
            for pid in (7, 8):
                s.add(Project(id=pid, slug=f"p{pid}", title="t", topic="x", status=ProjectStatus.new))
            await s.flush()
            s.add(Frame(id=5, project_id=owner, number=1, voiceover_text="v", uuid=f"u{owner}"))
            await s.commit()
        return eng, S

    m_eng, MasterS = await make(outside_tmp / "m.db", 8)
    p_eng, ProjS = await make(outside_tmp / "p.db", 7)
    import app.db as app_db
    monkeypatch.setattr(app_db, "SessionLocal", MasterS)
    monkeypatch.setitem(project_db._SESSION_MAKERS, "p", ProjS)
    project_db._FRAME_TO_PROJECT.clear()
    with pytest.raises(g.IdCollisionError):
        await project_db.resolve_frame_project_id(5)
    assert 5 not in project_db._FRAME_TO_PROJECT  # неоднозначный ответ в кэш не попадает

    # без коллизии (оба говорят «проект 8») ответ обычный и кэшируется
    monkeypatch.setitem(project_db._SESSION_MAKERS, "p", MasterS)
    assert await project_db.resolve_frame_project_id(5) == 8
    assert project_db._FRAME_TO_PROJECT[5] == 8
    project_db._SESSION_MAKERS.pop("p", None)
    await m_eng.dispose()
    await p_eng.dispose()
