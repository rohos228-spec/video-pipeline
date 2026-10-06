"""Versioned migrations for the tables the orchestrator owns.

Each database keeps a ``schema_migrations`` table (id, applied_at, checksum). Migrations run in id order,
each in its own savepoint together with its bookkeeping row: a failing migration leaves no partial schema and
no row, stops the run and is reported (``MigrationFailed``). Migrations are idempotent, so a database created
before the runner existed (tables already there) is simply recorded; nothing is altered or dropped. The
checksum is a fingerprint of the tables/triggers the migration owns; a recorded checksum that no longer
matches is reported as drift (the model changed without a new migration) and never rewritten.
Legacy and non-orchestrator tables are not touched here.
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
