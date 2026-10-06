"""Versioned migrations for the tables the orchestrator owns.

Each database keeps a ``schema_migrations`` table (id, applied_at, checksum). Migrations run in id order,
each in its own savepoint together with its bookkeeping row: a failing migration leaves no partial schema and
no row, stops the run and is reported (``MigrationFailed``). Migrations are idempotent, so a database created
before the runner existed (tables already there) is simply recorded; nothing is altered or dropped. The
checksum is a fingerprint of the tables/triggers the migration owns; a recorded checksum that no longer
matches is reported as drift (the model changed without a new migration) and never rewritten.
Migrations 0007+ move the ad-hoc startup ``ALTER TABLE ... ADD COLUMN`` of older tables (projects, frames,
frames_space) here, unchanged in result: the same columns, the same DDL, only recorded, checksummed and rolled
back as a unit. Other legacy tables are not touched here.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable

from loguru import logger
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

MIGRATIONS_TABLE = "schema_migrations"
SCOPE_MASTER = "master"
SCOPE_PROJECT = "project"

_CREATE_TABLE = (f"CREATE TABLE IF NOT EXISTS {MIGRATIONS_TABLE} ("
                 "id VARCHAR(80) NOT NULL PRIMARY KEY, applied_at TIMESTAMP NOT NULL, "
                 "checksum VARCHAR(64) NOT NULL)")


@dataclass(frozen=True)
class Migration:
    id: str
    description: str
    apply: Callable[..., Awaitable[None]]
    fingerprint: Callable[[], str]
    scopes: tuple[str, ...] = (SCOPE_MASTER,)


@dataclass
class MigrationReport:
    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    failed: str | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return {"applied": self.applied, "skipped": self.skipped, "drift": self.drift,
                "unknown": self.unknown, "failed": self.failed, "error": self.error}


class MigrationFailed(RuntimeError):
    def __init__(self, report: MigrationReport):
        self.report = report
        super().__init__(f"schema migration {report.failed} failed: {report.error}")


def _digest(parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def tables_fingerprint(tables, triggers=(), extra=()) -> str:
    shape = {t.name: sorted((c.name, str(c.type), bool(c.nullable), bool(c.primary_key)) for c in t.columns)
             for t in tables}
    return _digest({"tables": shape, "triggers": sorted(triggers), "extra": sorted(map(str, extra))})


async def _create_tables(conn, tables, triggers=()) -> None:
    from app.models import Base
    await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=list(tables), checkfirst=True))
    if conn.dialect.name == "sqlite":
        for ddl in triggers:
            await conn.exec_driver_sql(ddl)


async def _observation(conn) -> None:
    from app.services.observation import migrate_observation_schema
    await migrate_observation_schema(conn)


def _observation_fp() -> str:
    from app.services.observation import observation_tables
    return tables_fingerprint(observation_tables())


async def _spend_ledger(conn) -> None:
    from app.services import spend_guard
    await _create_tables(conn, [spend_guard.SpendLedger.__table__], spend_guard._TRIGGERS)


def _spend_ledger_fp() -> str:
    from app.services import spend_guard
    return tables_fingerprint([spend_guard.SpendLedger.__table__], spend_guard._TRIGGERS)


async def _provider_task_ledger(conn) -> None:
    from app.services import spend_guard
    await _create_tables(conn, [spend_guard.ProviderTaskLedger.__table__], spend_guard._TASK_TRIGGERS)


def _provider_task_ledger_fp() -> str:
    from app.services import spend_guard
    return tables_fingerprint([spend_guard.ProviderTaskLedger.__table__], spend_guard._TASK_TRIGGERS)


async def _action_layer(conn) -> None:
    from app.services.action_layer.store import ensure_action_layer_schema
    await ensure_action_layer_schema(conn)


def _action_layer_fp() -> str:
    from app.services.action_layer import store
    return tables_fingerprint(store._TABLES, store._TRIGGERS)


async def _change_control(conn) -> None:
    from app.services.change_control.store import ensure_change_control_schema
    await ensure_change_control_schema(conn)


def _change_control_fp() -> str:
    from app.services.change_control import store
    return tables_fingerprint(store._TABLES, store._TRIGGERS, store._ADDED_COLUMNS)


async def _action_attachments(conn) -> None:
    from app.services.action_layer.attachments import ensure_attachments_schema
    await ensure_attachments_schema(conn)


def _action_attachments_fp() -> str:
    from app.services.action_layer import attachments
    return tables_fingerprint(attachments._TABLES)


# --- 0007+: columns added by ALTER to tables that create_all already knows (exact legacy DDL) ---------------

FRAMES_V2_COLUMNS: tuple[tuple[str, str], ...] = (
    ("uuid", "VARCHAR(64)"),
    ("sort_key", "FLOAT"),
    ("scene_id", "INTEGER"),
)

FRAMES_SPACE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("manual", "INTEGER NOT NULL DEFAULT 0 CHECK (manual IN (0,1))"),
)

PROJECTS_COLUMNS: tuple[tuple[str, str], ...] = (
    ("image_generator", "VARCHAR(40)"),
    ("aspect_ratio", "VARCHAR(10)"),
    ("image_resolution", "VARCHAR(10)"),
    ("image_quality", "VARCHAR(10)"),
    ("image_relax", "BOOLEAN DEFAULT 0"),
    ("video_generator", "VARCHAR(40)"),
    ("video_resolution", "VARCHAR(10)"),
    ("video_relax", "BOOLEAN DEFAULT 0"),
    ("hero_count", "INTEGER"),
    ("hero_descriptions", "JSON"),
    ("hero_variations", "JSON"),
    ("hero_variation_modifiers", "JSON"),
    ("prompt_overrides", "JSON"),
    ("gpt_text_overrides", "JSON"),
    ("enrich_slots_count", "INTEGER DEFAULT 3"),
    ("item_descriptions", "JSON"),
    ("item_variations", "JSON"),
    ("batch_id", "INTEGER"),
    ("batch_position", "INTEGER"),
    ("batch_slug", "VARCHAR(120)"),
    ("auto_mode", "BOOLEAN DEFAULT 0"),
    ("title", "VARCHAR(240)"),
)


async def add_missing_columns(conn, table: str, columns) -> list[str]:
    """Idempotent ``ALTER TABLE ADD COLUMN`` of the columns the table lacks; returns the added names.

    A table that does not exist yet is left alone: whoever creates it (create_all / raw DDL) creates it with
    every column already. Any error propagates so the surrounding migration rolls back as a unit.
    """
    have = {r[1] for r in (await conn.exec_driver_sql(f"PRAGMA table_info({table})")).fetchall()}
    if not have:
        return []
    added: list[str] = []
    for name, ddl in columns:
        if name in have:
            continue
        await conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        added.append(name)
        logger.info("schema migration: {}.{} added", table, name)
    return added


def _columns_fp(table: str, columns) -> str:
    return _digest({"table": table, "added_columns": [list(c) for c in columns]})


async def _frames_v2_columns(conn) -> None:
    await add_missing_columns(conn, "frames", FRAMES_V2_COLUMNS)


def _frames_v2_columns_fp() -> str:
    return _columns_fp("frames", FRAMES_V2_COLUMNS)


async def _frames_space_manual(conn) -> None:
    await add_missing_columns(conn, "frames_space", FRAMES_SPACE_COLUMNS)


def _frames_space_manual_fp() -> str:
    return _columns_fp("frames_space", FRAMES_SPACE_COLUMNS)


async def _projects_columns(conn) -> None:
    await add_missing_columns(conn, "projects", PROJECTS_COLUMNS)


def _projects_columns_fp() -> str:
    return _columns_fp("projects", PROJECTS_COLUMNS)


MIGRATIONS: tuple[Migration, ...] = (
    Migration("0001_observation_core", "observation, diagnosis, actions, decision, profiles, media, "
              "managed launch, kill switch tables", _observation, _observation_fp,
              (SCOPE_MASTER, SCOPE_PROJECT)),
    Migration("0002_spend_ledger", "append-only spend ledger", _spend_ledger, _spend_ledger_fp),
    Migration("0003_provider_task_ledger", "append-only provider task ledger", _provider_task_ledger,
              _provider_task_ledger_fp),
    Migration("0004_action_layer", "action layer plans, approvals, history, spend, macros", _action_layer,
              _action_layer_fp),
    Migration("0005_change_control", "change proposals, config versions and pointers", _change_control,
              _change_control_fp),
    Migration("0006_action_attachments", "files attached to the action layer command line",
              _action_attachments, _action_attachments_fp),
    Migration("0007_frames_v2_columns", "frames.uuid / sort_key / scene_id (db v2 cards)",
              _frames_v2_columns, _frames_v2_columns_fp, (SCOPE_MASTER, SCOPE_PROJECT)),
    Migration("0008_frames_space_manual", "frames_space.manual flag (scene space)",
              _frames_space_manual, _frames_space_manual_fp, (SCOPE_MASTER, SCOPE_PROJECT)),
    Migration("0009_projects_columns", "projects settings columns added after the first release",
              _projects_columns, _projects_columns_fp),
)


def _validate(migrations) -> None:
    ids = [m.id for m in migrations]
    if ids != sorted(ids) or len(set(ids)) != len(ids):
        raise ValueError("migration ids must be unique and in ascending order")


async def _recorded(conn) -> dict[str, str]:
    rows = (await conn.exec_driver_sql(f"SELECT id, checksum FROM {MIGRATIONS_TABLE}")).fetchall()
    return {r[0]: r[1] for r in rows}


async def applied_migrations(conn) -> list[dict]:
    """Read-only view of what a database has recorded (empty if the runner never ran)."""
    def has_table(sync_conn):
        from sqlalchemy import inspect
        return inspect(sync_conn).has_table(MIGRATIONS_TABLE)
    if not await conn.run_sync(has_table):
        return []
    rows = (await conn.exec_driver_sql(
        f"SELECT id, applied_at, checksum FROM {MIGRATIONS_TABLE} ORDER BY id")).fetchall()
    return [{"id": r[0], "applied_at": str(r[1]), "checksum": r[2]} for r in rows]


async def run_migrations(conn, *, scope: str = SCOPE_MASTER,
                         migrations: tuple[Migration, ...] | None = None) -> MigrationReport:
    """Apply pending migrations of ``scope`` on an open (transactional) async connection.

    Raises MigrationFailed after rolling back the failing migration; earlier ones stay applied.
    """
    plan = tuple(m for m in (MIGRATIONS if migrations is None else migrations) if scope in m.scopes)
    _validate(plan)
    report = MigrationReport()
    await conn.exec_driver_sql(_CREATE_TABLE)
    done = await _recorded(conn)
    known = {m.id for m in plan}
    report.unknown = sorted(i for i in done if i not in known)
    for migration in plan:
        checksum = migration.fingerprint()
        if migration.id in done:
            report.skipped.append(migration.id)
            if done[migration.id] != checksum:
                report.drift.append(migration.id)
                logger.warning("schema migration {} drift: schema changed since it was applied; "
                               "add a new migration", migration.id)
            continue
        try:
            async with conn.begin_nested():
                await migration.apply(conn)
                await conn.execute(
                    text(f"INSERT INTO {MIGRATIONS_TABLE} (id, applied_at, checksum) "
                         "VALUES (:id, :at, :checksum)"),
                    {"id": migration.id, "at": datetime.now(timezone.utc).replace(tzinfo=None), "checksum": checksum})
        except IntegrityError:
            if migration.id in await _recorded(conn):
                report.skipped.append(migration.id)
                continue
            report.failed, report.error = migration.id, "IntegrityError"
            logger.error("schema migration {} failed and was rolled back", migration.id)
            raise MigrationFailed(report)
        except Exception as exc:  # noqa: BLE001
            report.failed, report.error = migration.id, f"{type(exc).__name__}: {exc}"[:300]
            logger.error("schema migration {} failed and was rolled back: {}", migration.id, report.error)
            raise MigrationFailed(report) from exc
        report.applied.append(migration.id)
        logger.info("schema migration {} applied: {}", migration.id, migration.description)
    return report


async def migrate_orchestrator_schema(conn, *, scope: str = SCOPE_MASTER) -> MigrationReport:
    """Startup entry point replacing the separate ensure/create_all calls for orchestrator tables."""
    return await run_migrations(conn, scope=scope)


async def run_migration_ids(conn, ids, *, scope: str = SCOPE_PROJECT) -> MigrationReport:
    """Run only the named registered migrations (for narrow legacy entry points); same bookkeeping."""
    wanted = tuple(m for m in MIGRATIONS if m.id in set(ids))
    return await run_migrations(conn, scope=scope, migrations=wanted)
