from __future__ import annotations

import asyncio
import contextlib
import os
import sqlite3
from pathlib import Path

from loguru import logger

ROOT = Path(__file__).resolve().parents[2]
BACKUP_ENV = "VP_SCHEDULED_BACKUP"

SCOPED_TABLES: tuple[str, ...] = (
    "scenes", "frames", "frame_texts", "prompt_versions", "frame_edges", "artifacts",
    "entities", "hitl_requests", "scene_design_cells", "asr_words", "attempts",
)


class IdCollisionError(RuntimeError):
    pass


_COLLISIONS: dict[int, list[dict]] = {}
_CHECKED: set[int] = set()


def collisions_for(project_id: int) -> list[dict]:
    return list(_COLLISIONS.get(project_id, ()))


def reset_state() -> None:
    _COLLISIONS.clear()
    _CHECKED.clear()



def project_dir(data_root: Path, slug: str, batch_id: int | None, batch_slug: str | None) -> Path:
    if batch_id is not None and batch_slug:
        return data_root / "batches" / batch_slug / "sub" / slug
    return data_root / "videos" / slug


def project_db_files(state_path: Path | str, data_root: Path | str | None = None) -> list[Path]:
    state = Path(state_path)
    if not state.is_file():
        return []
    if data_root is None:
        from app.settings import settings
        data_root = settings.data_dir
    out: list[Path] = []
    try:
        uri = f"file:{state.as_posix()}?mode=ro"
        with contextlib.closing(sqlite3.connect(uri, uri=True, timeout=10)) as db:
            rows = db.execute("SELECT slug, batch_id, batch_slug FROM projects WHERE slug IS NOT NULL").fetchall()
    except sqlite3.Error as exc:
        logger.warning("backup targets: cannot list projects in {}: {}", state.name, exc)
        return []
    for slug, batch_id, batch_slug in rows:
        p = project_dir(Path(data_root), slug, batch_id, batch_slug) / "project.db"
        if p.is_file() and p not in out:
            out.append(p)
    return out


def backup_targets(state_path: Path | str | None = None, root: Path = ROOT,
                   data_root: Path | str | None = None) -> list[Path]:
    if state_path is None:
        from app.settings import settings
        state_path = settings.sqlite_path
    state = Path(state_path)
    return [state, root / "api-tracker" / "tracker.db", *project_db_files(state, data_root)]


def scheduled_backup_enabled(env: dict | None = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get(BACKUP_ENV, "1")).strip().lower() not in {"0", "false", "no", "off"}


def start_scheduled_backups(state_path: Path | str | None = None, *, interval_h: float = 6.0,
                            check_every_s: float = 600.0, stop: asyncio.Event | None = None,
                            data_root: Path | str | None = None):
    if not scheduled_backup_enabled():
        logger.info("scheduled backups disabled by {}=0", BACKUP_ENV)
        return None
    from app.services.schema_migrations import scheduled_backup_loop
    return asyncio.create_task(
        scheduled_backup_loop(lambda: backup_targets(state_path, data_root=data_root), interval_h=interval_h,
                              check_every_s=check_every_s, stop=stop),
        name="scheduled-db-backup")



async def migrate_existing_project_dbs() -> dict:
    from sqlalchemy import select

    from app.db import SessionLocal
    from app.models import Project
    from app.project_db import init_project_db, resolve_project_db_path

    report: dict = {"checked": 0, "failed": []}
    async with SessionLocal() as s:
        projects = (await s.execute(select(Project))).scalars().all()
        rows = [(p.id, p.slug, p.data_dir) for p in projects]
    for pid, slug, data_dir in rows:
        if not data_dir or not resolve_project_db_path(data_dir).is_file():
            continue
        report["checked"] += 1
        try:
            await init_project_db(data_dir)
        except Exception as exc:
            report["failed"].append({"project_id": pid, "slug": slug, "error": f"{type(exc).__name__}: {exc}"[:300]})
            logger.error("project.db schema migration failed for project #{} [{}]: {}", pid, slug, exc)
    return report



def find_id_collisions(master_db: Path | str, project_db: Path | str, project_id: int | None = None) -> list[dict]:
    master, proj = Path(master_db), Path(project_db)
    if not master.is_file() or not proj.is_file():
        return []
    found: list[dict] = []
    with contextlib.closing(sqlite3.connect(f"file:{master.as_posix()}?mode=ro", uri=True, timeout=30)) as db:
        db.execute("ATTACH DATABASE ? AS pdb", (f"file:{proj.as_posix()}?mode=ro",))

        def has_cols(schema: str, table: str) -> bool:
            cols = {r[1] for r in db.execute(f"PRAGMA {schema}.table_info({table})")}
            return {"id", "project_id"} <= cols

        for table in SCOPED_TABLES:
            try:
                if not (has_cols("main", table) and has_cols("pdb", table)):
                    continue
                q = (f"SELECT m.id, m.project_id, p.project_id FROM main.{table} m JOIN pdb.{table} p ON p.id = m.id "
                     f"WHERE m.project_id IS NOT p.project_id")
                args: tuple = ()
                if project_id is not None:
                    q += " AND (p.project_id = ? OR m.project_id = ?)"
                    args = (project_id, project_id)
                for rid, mpid, ppid in db.execute(q + " ORDER BY m.id", args):
                    found.append({"table": table, "id": rid, "master_project_id": mpid, "project_db_project_id": ppid})
            except sqlite3.Error as exc:
                logger.warning("id collision check skipped table {}: {}", table, exc)
    return found


def _master_path() -> Path | None:
    from app.db import engine
    name = engine.url.database
    return Path(name) if name else None


async def check_project_id_collisions(project_id: int, data_dir: Path | str | None = None, *, force: bool = False) -> list[dict]:
    if project_id in _CHECKED and not force:
        return collisions_for(project_id)
    from app.project_db import get_project_data_dir, resolve_project_db_path
    data_dir = data_dir if data_dir is not None else await get_project_data_dir(project_id)
    master = _master_path()
    if data_dir is None or master is None:
        return []
    found = await asyncio.to_thread(find_id_collisions, master, resolve_project_db_path(data_dir), project_id)
    _CHECKED.add(project_id)
    _COLLISIONS[project_id] = found
    if found:
        logger.error("ID COLLISION project #{}: {} rows have the same id in master and project.db with different "
                     "project_id (first: {}). Rows are NOT renumbered; lookups by these ids are refused.",
                     project_id, len(found), found[:3])
    return found


def ambiguity(project_ids: set[int | None]) -> bool:
    return len({p for p in project_ids if p is not None}) > 1
