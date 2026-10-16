from __future__ import annotations

import asyncio
import copy
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, NodeRun, Project, ProjectStatus, Workflow, WorkflowRun
from app.services import graph_migrations as gm

ROOT = Path(__file__).resolve().parents[1]
NODES = [
    {"id": "a", "type": "enrich_1", "position": {"x": 0, "y": 0}, "data": {"custom": [1]}},
    {"id": "b", "type": "enrich_4", "position": {"x": 1, "y": 0}, "data": {"label": "Мой"}},
    {"id": "c", "type": "hero", "position": {"x": 2, "y": 0}, "data": {}},
]


def _make_db(path: Path) -> None:
    async def go():
        engine = create_async_engine(f"sqlite+aiosqlite:///{path.as_posix()}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as s:
            wf = Workflow(name="w", nodes=copy.deepcopy(NODES), edges=[], version=1, meta={})
            s.add(wf)
            await s.flush()
            s.add(Project(topic="t", slug="w46", status=ProjectStatus.enrich_1_ready,
                          meta={"canvas_graph": {"nodes": copy.deepcopy(NODES), "edges": []}}))
            run = WorkflowRun(workflow_id=wf.id, project_id=1, nodes_snapshot=copy.deepcopy(NODES),
                              edges_snapshot=[], meta={})
            s.add(run)
            await s.flush()
            s.add(NodeRun(workflow_run_id=run.id, node_key="a", node_type="enrich_1"))
            await s.commit()
        await engine.dispose()

    asyncio.run(go())


def _types(path: Path) -> list[str]:
    con = sqlite3.connect(path)
    try:
        return [r[0] for r in con.execute("select nodes from workflows")] + \
               [r[0] for r in con.execute("select node_type from node_runs")]
    finally:
        con.close()


def _cli(*args: str, cwd: Path | None = None):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "migrate_enrich_graphs.py"), *args],
                          capture_output=True, text=True, encoding="utf-8", cwd=cwd or ROOT,
                          env={**__import__("os").environ, "PYTHONUTF8": "1"})


@pytest.fixture
def db(outside_tmp):
    p = outside_tmp / "copy.db"
    _make_db(p)
    return p


def test_cli_dry_run_is_default_and_writes_nothing(db):
    before = db.read_bytes()
    r = _cli("--db", str(db))
    assert r.returncode == 0, r.stderr
    assert "СУХОЙ ПРОХОД" in r.stdout and "enrich_1 -> excel_gpt" in r.stdout
    assert "Всего enrich_N в базе: 6" in r.stdout
    assert db.read_bytes() == before, "сухой проход не меняет файл базы"
    assert not (db.parent / "backups").exists(), "сухой проход не делает копий"


def test_cli_apply_makes_verified_backup_then_rollback_restores(db):
    r = _cli("--db", str(db), "--apply")
    assert r.returncode == 0, r.stderr
    assert "ПРИМЕНЕНО" in r.stdout and "проверка пройдена" in r.stdout and "осталось enrich_N: 0" in r.stdout
    backups = list((db.parent / "backups").glob("*.db"))
    assert len(backups) == 1
    assert "enrich_1" in "".join(_types(backups[0])), "в копии исходные узлы"
    assert "enrich_" not in "".join(_types(db))
    again = _cli("--db", str(db), "--apply")
    assert again.returncode == 0 and "менять нечего" in again.stdout
    back = _cli("--db", str(db), "--rollback")
    assert back.returncode == 0, back.stderr
    assert "ОТКАТ: возвращено узлов: 6" in back.stdout and "осталось enrich_N: 6" in back.stdout
    assert "enrich_1" in "".join(_types(db)) and "enrich_4" in "".join(_types(db))


def test_cli_refuses_live_write_without_confirm(tmp_path):
    r = _cli("--apply", cwd=tmp_path)
    assert r.returncode == 2 and "--confirm-live" in r.stderr
    r = _cli("--rollback", cwd=tmp_path)
    assert r.returncode == 2 and "--confirm-live" in r.stderr
    assert not (tmp_path / "data").exists()


def test_cli_refuses_write_to_db_inside_repo_data_dir_without_confirm():
    probe = ROOT / "data" / "_c10_guard_probe.db"
    for flag in ("--apply", "--rollback"):
        r = _cli("--db", str(probe), flag)
        assert r.returncode == 2 and "--confirm-live" in r.stderr, r.stderr
    assert not probe.exists()
    r = _cli("--db", str(probe))
    assert r.returncode == 2 and "не найден" in r.stderr


def test_cli_missing_db_is_reported(tmp_path):
    r = _cli("--db", str(tmp_path / "none.db"))
    assert r.returncode == 2 and "не найден" in r.stderr


@pytest.mark.asyncio
async def test_run_migration_failed_verification_auto_rolls_back(db, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    real = gm.migrate_stores
    calls = {"n": 0}

    async def flaky(session, *, dry_run=True):
        calls["n"] += 1
        out = await real(session, dry_run=dry_run)
        if dry_run and calls["n"] == 2:
            out["nodes_changed"] = 1
        return out

    monkeypatch.setattr(gm, "migrate_stores", flaky)
    out = await gm.run_migration(factory, db, apply=True, backup_dir=db.parent / "bk")
    await engine.dispose()
    assert out["verified"] is False and out["auto_rolled_back"] is True
    assert "enrich_1" in "".join(_types(db))
    assert Path(out["backup"]).is_file()


@pytest.mark.asyncio
async def test_apply_and_rollback_conflict_and_missing_file(tmp_path):
    with pytest.raises(gm.MigrationError):
        await gm.run_migration(None, tmp_path / "x.db", apply=True, rollback=True)
    with pytest.raises(gm.MigrationError, match="не найден"):
        gm.backup_database(tmp_path / "x.db")


def test_report_ru_lists_skips_and_empty():
    text = gm.format_report_ru({"dry_run": True, "stores_changed": 1, "nodes_changed": 1, "items": [
        {"store": "workflow", "id": 7, "changed": True,
         "changes": [{"node_id": "n1", "from_type": "enrich_2", "to_type": "excel_gpt", "slot_before": 2,
                      "slot_after": 2, "slot_index_added": True, "label_changed": True}],
         "skipped": [{"node_id": "n2", "reason": "slot would change 1 -> 2"}]}]})
    assert "изменится" in text and "шаблон графа #7" in text and "ПРОПУЩЕН узел n2" in text
    assert "Узлов enrich_N не найдено" in gm.format_report_ru({"dry_run": False, "items": []})


def test_dead_improve_cell_scene_duplicate_is_gone():
    direct = (ROOT / "app/services/montage_scene_direct.py").read_text(encoding="utf-8")
    assert "def improve_cell_scene" not in direct
    live = (ROOT / "app/services/montage_scene_improve.py").read_text(encoding="utf-8")
    assert "async def improve_cell_scene" in live
    import app.services.montage_scene_direct as d
    assert callable(d.build_kadry) and callable(d.pick_scene_characters)
