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

Backups (step A15). ``migrate_orchestrator_schema`` copies an existing file database BEFORE its first pending
migration (sqlite online backup, safe under WAL), checks the copy (integrity, row counts, sha256 manifest) and
keeps a separate copy of the journal tables (spend / observation / action-layer history). A failing migration
still rolls back by itself; the report names the backup and the restore command. ``restore_backup`` puts a
verified copy back (the replaced file is moved aside, never deleted); ``run_scheduled_backups`` and
``scheduled_backup_loop`` make periodic copies. The command line is ``scripts/backup_db.py``.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path
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
    backup: str | None = None
    journal_backup: str | None = None

    def as_dict(self) -> dict:
        out = {"applied": self.applied, "skipped": self.skipped, "drift": self.drift,
               "unknown": self.unknown, "failed": self.failed, "error": self.error}
        if self.backup:
            out["backup"] = self.backup
            out["journal_backup"] = self.journal_backup
        return out


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


# Tables added to the action layer after 0004 was released (W5): they belong to 0016, so the 0004 fingerprint of
# databases that already applied it does not change (no false drift).
_ACTION_LAYER_W5_TABLES = ("action_layer_calls", "action_layer_snapshots")


def _action_layer_fp() -> str:
    from app.services.action_layer import store
    return tables_fingerprint([t for t in store._TABLES if t.name not in _ACTION_LAYER_W5_TABLES], store._TRIGGERS)


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


async def _change_proposals_reject_columns(conn) -> None:
    from app.services.change_control.store import _ADDED_COLUMNS
    # _ADDED_COLUMNS holds "name DDL" strings; split to the (name, ddl) pairs add_missing_columns takes.
    await add_missing_columns(conn, "change_proposals",
                              [(name, ddl[len(name):].strip()) for name, ddl in _ADDED_COLUMNS])


def _change_proposals_reject_columns_fp() -> str:
    from app.services.change_control.store import _ADDED_COLUMNS
    return _columns_fp("change_proposals", _ADDED_COLUMNS)


async def _operator_audit(conn) -> None:
    from app.services import operator_audit
    await _create_tables(conn, [operator_audit.OperatorAudit.__table__], operator_audit._TRIGGERS)


def _operator_audit_fp() -> str:
    from app.services import operator_audit
    return tables_fingerprint([operator_audit.OperatorAudit.__table__], operator_audit._TRIGGERS)


async def _publications(conn) -> None:
    # Тот же DDL, что у publications_store.ensure_schema: CREATE ... IF NOT EXISTS, поэтому таблица,
    # уже созданная самим хранилищем, миграцию не ломает.
    from app.services import publications_store
    if conn.dialect.name == "sqlite":
        for ddl in publications_store._DDL:
            await conn.exec_driver_sql(ddl)


def _publications_fp() -> str:
    from app.services import publications_store
    return _digest({"publications_ddl": [" ".join(d.split()) for d in publications_store._DDL]})


def _ddl_statements(script) -> list[str]:
    """DDL-скрипт (строка или кортеж операторов) -> список отдельных операторов (триггеры с BEGIN..END целиком)."""
    parts = [script] if isinstance(script, str) else list(script)
    out: list[str] = []
    for part in parts:
        buf = ""
        for line in part.splitlines(keepends=True):
            buf += line
            if sqlite3.complete_statement(buf):
                if buf.strip():
                    out.append(buf.strip())
                buf = ""
        if buf.strip():
            out.append(buf.strip())
    return out


async def _notification_log(conn) -> None:
    # Тот же DDL, что создаёт notification_log.py сам (IF NOT EXISTS): уже созданная модулем таблица не мешает.
    from app.services import notification_log
    if conn.dialect.name == "sqlite":
        for ddl in _ddl_statements(notification_log.NOTIFICATION_LOG_DDL):
            await conn.exec_driver_sql(ddl)


def _notification_log_fp() -> str:
    from app.services import notification_log
    return _digest({"notification_log_ddl": [" ".join(d.split()) for d in _ddl_statements(notification_log.NOTIFICATION_LOG_DDL)]})


async def _journal_archive_log(conn) -> None:
    from app.services import journal_archive
    if conn.dialect.name == "sqlite":
        for ddl in _ddl_statements(journal_archive.ARCHIVE_LOG_DDL):
            await conn.exec_driver_sql(ddl)


def _journal_archive_log_fp() -> str:
    from app.services import journal_archive
    return _digest({"journal_archive_log_ddl": [" ".join(d.split()) for d in _ddl_statements(journal_archive.ARCHIVE_LOG_DDL)]})


async def _create_jobs(conn) -> None:
    from app.services import create_jobs
    if conn.dialect.name == "sqlite":
        for ddl in _ddl_statements(create_jobs.CREATE_JOBS_DDL):
            await conn.exec_driver_sql(ddl)


def _create_jobs_fp() -> str:
    from app.services import create_jobs
    return _digest({"create_jobs_ddl": [" ".join(d.split()) for d in _ddl_statements(create_jobs.CREATE_JOBS_DDL)]})


async def _outsee_idempotency(conn) -> None:
    # Same definition the module creates for itself on first use (IF NOT EXISTS / checkfirst): a table created by
    # the module earlier does not break the migration. The action layer's idempotency / snapshot tables ride along.
    from app.services import outsee_idempotency
    from app.services.action_layer import store
    tables = [outsee_idempotency.OutseeIdempotency.__table__] + [
        t for t in store._TABLES if t.name in _ACTION_LAYER_W5_TABLES]
    await _create_tables(conn, tables, outsee_idempotency._TRIGGERS)


def _outsee_idempotency_fp() -> str:
    from app.services import outsee_idempotency
    from app.services.action_layer import store
    tables = [outsee_idempotency.OutseeIdempotency.__table__] + [
        t for t in store._TABLES if t.name in _ACTION_LAYER_W5_TABLES]
    return tables_fingerprint(tables, outsee_idempotency._TRIGGERS)


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
    Migration("0010_change_proposals_reject_columns", "change_proposals.rejected_by / reject_reason / rejected_at",
              _change_proposals_reject_columns, _change_proposals_reject_columns_fp),
    Migration("0011_operator_audit", "append-only operator audit of mutating /api requests",
              _operator_audit, _operator_audit_fp),
    Migration("0012_publications", "publications journal (idempotency, intent before click, lease)",
              _publications, _publications_fp),
    Migration("0013_notification_log", "append-only log of messages sent to the operator",
              _notification_log, _notification_log_fp),
    Migration("0014_journal_archive_log", "append-only log of journal archiving (the only allowed delete path)",
              _journal_archive_log, _journal_archive_log_fp),
    Migration("0015_create_jobs", "durable state of Create jobs (survives restart)",
              _create_jobs, _create_jobs_fp),
    Migration("0016_outsee_idempotency", "append-only Outsee request-key log; action layer call journal and snapshots",
              _outsee_idempotency, _outsee_idempotency_fp),
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


async def migrate_orchestrator_schema(conn, *, scope: str = SCOPE_MASTER, backup: bool = True) -> MigrationReport:
    """Startup entry point replacing the separate ensure/create_all calls for orchestrator tables.

    Before the first pending migration of an existing file database a verified copy is made (fail closed: if the
    copy cannot be made nothing is migrated and ``BackupError`` is raised).
    """
    made: BackupResult | None = None
    if backup:
        path = await _conn_db_path(conn)
        if path is not None:
            made = await asyncio.to_thread(backup_before_migrations, path, scope)
    try:
        report = await run_migrations(conn, scope=scope)
    except MigrationFailed as exc:
        if made is not None:
            exc.report.backup, exc.report.journal_backup = str(made.path), _str_or_none(made.journal_path)
            logger.error("schema migration {} failed; the failing step was rolled back, the pre-migration copy is "
                         "{}. Restore command: {}", exc.report.failed, made.path,
                         restore_command(made.path, made.source))
        raise
    if made is not None:
        report.backup, report.journal_backup = str(made.path), _str_or_none(made.journal_path)
    return report


async def run_migration_ids(conn, ids, *, scope: str = SCOPE_PROJECT) -> MigrationReport:
    """Run only the named registered migrations (for narrow legacy entry points); same bookkeeping."""
    wanted = tuple(m for m in MIGRATIONS if m.id in set(ids))
    return await run_migrations(conn, scope=scope, migrations=wanted)


# --- A15: backups before migrations, on a schedule, restore and restore check --------------------------------

BACKUP_DIR_NAME = "backups"
BACKUP_SUFFIX = ".bak.db"
KEEP_PRE_MIGRATION = 10
KEEP_SCHEDULED = 14
# Journal tables (append-only records of money, observation and operator actions) copied on their own so a
# damaged main copy never takes the journal with it.
JOURNAL_TABLES: tuple[str, ...] = (
    "spend_ledger", "provider_task_ledger",
    "observation_executions", "observation_outbox", "observation_events", "observation_degradations",
    "observation_commit_uncertainty",
    "action_layer_plans", "action_layer_steps", "action_layer_approvals", "action_layer_spend",
    "action_layer_history", "action_layer_plan_context",
    "change_proposals", "config_versions", "config_pointers", "schema_migrations",
    "notification_log", "journal_archive_log", "operator_audit", "publications",
    "outsee_idempotency", "action_layer_calls", "action_layer_snapshots",
)


class BackupError(RuntimeError):
    """A backup could not be made or did not pass its check (the caller must not go on to migrate)."""


class RestoreError(RuntimeError):
    """A restore was refused; the target file is unchanged."""


@dataclass
class BackupResult:
    path: Path
    source: Path
    manifest_path: Path
    sha256: str
    size: int
    tables: dict
    tag: str
    journal_path: Path | None = None


def _str_or_none(p) -> str | None:
    return str(p) if p is not None else None


def _safe_tag(tag: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", tag.lower()).strip("-") or "manual"


def _stamp() -> str:
    now = time.time()
    return time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + f"-{int(now * 1000) % 1000:03d}"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _user_tables(db: sqlite3.Connection) -> list[str]:
    return [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _table_counts(db: sqlite3.Connection) -> dict[str, int]:
    return {t: db.execute(f"SELECT count(*) FROM {_quote(t)}").fetchone()[0] for t in _user_tables(db)}


def _integrity(db: sqlite3.Connection) -> str:
    rows = [r[0] for r in db.execute("PRAGMA integrity_check")]
    return "ok" if rows == ["ok"] else "; ".join(map(str, rows))[:300]


def default_backup_dir(db_path: Path | str) -> Path:
    return Path(db_path).resolve().parent / BACKUP_DIR_NAME


def backup_files(dest_dir: Path | str, stem: str, tag: str | None = None) -> list[Path]:
    """Backups of ``stem`` in ``dest_dir``, oldest first (names sort by time). Journal copies are separate."""
    d = Path(dest_dir)
    if not d.is_dir():
        return []
    prefix = f"{stem}." + (f"{_safe_tag(tag)}." if tag else "")
    return sorted(p for p in d.iterdir() if p.name.startswith(prefix) and p.name.endswith(BACKUP_SUFFIX)
                  and not p.name.startswith(f"{stem}.journal."))


def latest_backup(dest_dir: Path | str, stem: str, tag: str | None = None) -> Path | None:
    files = backup_files(dest_dir, stem, tag)
    return files[-1] if files else None


def prune_backups(dest_dir: Path | str, stem: str, tag: str, keep: int) -> list[Path]:
    """Delete the oldest backups of this tag beyond ``keep`` (and their manifests). Only our own names."""
    removed: list[Path] = []
    keep = max(1, int(keep))
    d = Path(dest_dir)
    for kind in (f"{stem}.{_safe_tag(tag)}.", f"{stem}.journal.{_safe_tag(tag)}."):
        files = sorted(d.glob(kind + "*" + BACKUP_SUFFIX)) if d.is_dir() else []
        for old in files[:-keep]:
            for victim in (old, old.with_name(old.name + ".json")):
                with contextlib.suppress(OSError):
                    victim.unlink()
            removed.append(old)
    return removed


def _has_user_data(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        with contextlib.closing(sqlite3.connect(str(path), timeout=30)) as db:
            return any(t != MIGRATIONS_TABLE for t in _user_tables(db))
    except sqlite3.Error:
        return True  # unreadable but non-empty: treat as data worth copying (the copy will then fail loudly)


def _check_space(dest_dir: Path, src: Path) -> None:
    need = src.stat().st_size
    wal = src.with_name(src.name + "-wal")
    if wal.exists():
        need += wal.stat().st_size
    free = shutil.disk_usage(dest_dir).free
    if free < int(need * 1.25) + (16 << 20):
        raise BackupError(f"мало места для копии базы {src.name}: нужно около {need >> 20} МБ, свободно {free >> 20} МБ")


def backup_sqlite(src: Path | str, dest_dir: Path | str | None = None, *, tag: str = "manual",
                  keep: int | None = None, journal: bool = True) -> BackupResult:
    """Consistent copy of a live SQLite file (online backup API, safe while the app writes in WAL mode).

    The copy is written under a ``.part`` name, checked (integrity_check, row counts) and only then renamed;
    a sha256 manifest sits next to it. Raises ``BackupError``; never leaves a half-written ``.bak.db``.
    """
    src = Path(src).resolve()
    if not src.is_file():
        raise BackupError(f"нет файла базы: {src}")
    dest_dir = Path(dest_dir) if dest_dir else default_backup_dir(src)
    tag = _safe_tag(tag)
    stem = src.stem
    suffix = f"{tag}.{_stamp()}{BACKUP_SUFFIX}"
    final = dest_dir / f"{stem}.{suffix}"
    part = final.with_name(final.name + ".part")
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        _check_space(dest_dir, src)
        with contextlib.closing(sqlite3.connect(str(src), timeout=60)) as source, \
                contextlib.closing(sqlite3.connect(str(part))) as target:
            source.backup(target)
        with contextlib.closing(sqlite3.connect(str(part))) as copy:
            verdict = _integrity(copy)
            if verdict != "ok":
                raise BackupError(f"копия {src.name} не прошла integrity_check: {verdict}")
            tables = _table_counts(copy)
        os.replace(part, final)
        digest = _sha256_file(final)
        manifest = final.with_name(final.name + ".json")
        manifest.write_text(json.dumps({
            "source": str(src), "created_at": datetime.now(timezone.utc).isoformat(), "tag": tag,
            "sha256": digest, "size": final.stat().st_size, "tables": tables, "integrity": "ok"},
            ensure_ascii=False, indent=2), encoding="utf-8")
    except BackupError:
        with contextlib.suppress(OSError):
            part.unlink()
        raise
    except Exception as exc:  # noqa: BLE001
        with contextlib.suppress(OSError):
            part.unlink()
        raise BackupError(f"не удалось скопировать {src.name}: {type(exc).__name__}: {exc}"[:400]) from exc
    result = BackupResult(final, src, manifest, digest, final.stat().st_size, tables, tag)
    if journal:
        try:
            result.journal_path = extract_journal(final, dest_dir / f"{stem}.journal.{suffix}")
        except Exception as exc:  # noqa: BLE001  the full copy exists; a missing journal copy is loud, not fatal
            logger.error("journal copy of {} failed: {}", src.name, exc)
    if keep:
        prune_backups(dest_dir, stem, tag, keep)
    logger.info("db backup {} -> {} ({} tables)", src.name, final, len(tables))
    return result


def extract_journal(backup: Path | str, dest: Path | str) -> Path | None:
    """Copy only the journal tables (with their rows) of a verified backup into their own small database."""
    backup, dest = Path(backup), Path(dest)
    part = dest.with_name(dest.name + ".part")
    with contextlib.suppress(OSError):
        part.unlink()
    copied = 0
    with contextlib.closing(sqlite3.connect(str(backup))) as src, contextlib.closing(sqlite3.connect(str(part))) as out:
        have = set(_user_tables(src))
        for name in JOURNAL_TABLES:
            if name not in have:
                continue
            ddl = src.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()[0]
            out.execute(ddl)
            rows = src.execute(f"SELECT * FROM {_quote(name)}").fetchall()
            if rows:
                marks = ",".join("?" * len(rows[0]))
                out.executemany(f"INSERT INTO {_quote(name)} VALUES ({marks})", rows)
            copied += 1
        out.commit()
        verdict = _integrity(out)
    if copied == 0:
        with contextlib.suppress(OSError):
            part.unlink()
        return None
    if verdict != "ok":
        part.unlink()
        raise BackupError(f"копия журнала не прошла integrity_check: {verdict}")
    os.replace(part, dest)
    return dest


def verify_backup(path: Path | str) -> dict:
    """Restore check: sha256 against the manifest, then restore into a clean temp folder and read it back.

    Returns ``{"ok", "problems", "integrity", "tables"}``; never raises on a damaged file.
    """
    path = Path(path)
    problems: list[str] = []
    out = {"ok": False, "problems": problems, "integrity": None, "tables": {}}
    if not path.is_file():
        problems.append("файла копии нет")
        return out
    manifest: dict = {}
    mpath = path.with_name(path.name + ".json")
    if mpath.is_file():
        try:
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            problems.append("манифест копии не читается")
    else:
        problems.append("нет манифеста копии")
    if manifest.get("sha256") and manifest["sha256"] != _sha256_file(path):
        problems.append("sha256 не совпадает с манифестом: копия изменена или повреждена")
    tmp = Path(tempfile.mkdtemp(prefix="vp_restore_check_"))
    try:
        probe = tmp / "restored.db"
        shutil.copy2(path, probe)
        with contextlib.closing(sqlite3.connect(str(probe))) as db:
            out["integrity"] = _integrity(db)
            out["tables"] = _table_counts(db)
        if out["integrity"] != "ok":
            problems.append(f"integrity_check: {out['integrity']}")
        for name, count in (manifest.get("tables") or {}).items():
            if out["tables"].get(name) != count:
                problems.append(f"таблица {name}: в манифесте {count} строк, в копии {out['tables'].get(name)}")
    except sqlite3.Error as exc:
        problems.append(f"копия не открывается как база: {exc}"[:300])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out["ok"] = not problems
    return out


def restore_command(backup: Path | str, target: Path | str) -> str:
    return f'python scripts\\backup_db.py restore "{backup}" --target "{target}"'


def restore_backup(backup: Path | str, target: Path | str) -> dict:
    """Put a verified backup in place of ``target``.

    The backup is verified first; the copy is staged next to the target and checked again; the current file
    (with its ``-wal``/``-shm``) is MOVED aside as ``<name>.before-restore-<time>``, never deleted, so the restore
    itself can be undone. Stop Studio first: on Windows a file held open by another process cannot be moved and
    the restore stops with the target untouched.
    """
    backup, target = Path(backup), Path(target)
    check = verify_backup(backup)
    if not check["ok"]:
        raise RestoreError("копия не прошла проверку, восстановление отменено: " + "; ".join(check["problems"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(target.name + ".restore-part")
    stamp = _stamp()
    moved: list[tuple[Path, Path]] = []
    try:
        shutil.copy2(backup, staged)
        with contextlib.closing(sqlite3.connect(str(staged))) as db:
            verdict = _integrity(db)
        if verdict != "ok":
            raise RestoreError(f"подготовленная копия не прошла integrity_check: {verdict}")
        if target.exists():
            try:
                with contextlib.closing(sqlite3.connect(str(target), timeout=1)) as live:
                    live.execute("BEGIN EXCLUSIVE")
                    live.rollback()
            except sqlite3.Error as exc:
                raise RestoreError(f"база занята другим процессом (остановите Studio): {exc}") from exc
        for ext in ("", "-wal", "-shm"):
            cur = target.with_name(target.name + ext)
            if cur.exists():
                aside = target.with_name(f"{target.name}.before-restore-{stamp}{ext}")
                os.replace(cur, aside)
                moved.append((cur, aside))
        os.replace(staged, target)
    except Exception as exc:
        for cur, aside in reversed(moved):
            with contextlib.suppress(OSError):
                os.replace(aside, cur)
        with contextlib.suppress(OSError):
            staged.unlink()
        if isinstance(exc, RestoreError):
            raise
        raise RestoreError(f"восстановление не удалось, файл возвращён на место: {type(exc).__name__}: {exc}"[:400]) from exc
    return {"restored": str(target), "from": str(backup), "tables": check["tables"],
            "moved_aside": [str(a) for _, a in moved]}


def pending_migration_ids(db_path: Path | str, scope: str = SCOPE_MASTER,
                          migrations: tuple[Migration, ...] | None = None) -> list[str]:
    plan = [m.id for m in (MIGRATIONS if migrations is None else migrations) if scope in m.scopes]
    done: set[str] = set()
    with contextlib.closing(sqlite3.connect(str(db_path), timeout=30)) as db:
        if MIGRATIONS_TABLE in _user_tables(db):
            done = {r[0] for r in db.execute(f"SELECT id FROM {MIGRATIONS_TABLE}")}
    return [i for i in plan if i not in done]


def backup_before_migrations(db_path: Path | str, scope: str = SCOPE_MASTER,
                             migrations: tuple[Migration, ...] | None = None,
                             dest_dir: Path | str | None = None) -> BackupResult | None:
    """Copy ``db_path`` if it holds data and has a pending migration; ``None`` when nothing needs protecting."""
    path = Path(db_path)
    if not _has_user_data(path):
        return None
    try:
        if not pending_migration_ids(path, scope, migrations):
            return None
    except sqlite3.Error as exc:
        raise BackupError(f"не удалось прочитать список миграций {path.name}: {exc}") from exc
    return backup_sqlite(path, dest_dir, tag="pre-migration", keep=KEEP_PRE_MIGRATION)


async def _conn_db_path(conn) -> Path | None:
    rows = (await conn.exec_driver_sql("PRAGMA database_list")).fetchall()
    for r in rows:
        if r[1] == "main" and r[2]:
            return Path(r[2])
    return None


def backup_due(dest_dir: Path | str, stem: str, interval_h: float, tag: str = "scheduled") -> bool:
    last = latest_backup(dest_dir, stem, tag)
    if last is None:
        return True
    return (time.time() - last.stat().st_mtime) >= interval_h * 3600


def run_scheduled_backups(db_paths, *, interval_h: float = 6.0, keep: int = KEEP_SCHEDULED, force: bool = False,
                          dest_dir: Path | str | None = None) -> list[dict]:
    """One pass over the databases: copy those whose last scheduled copy is older than ``interval_h``, then
    run the restore check on the new copy. Failures are reported per database, never raised."""
    out: list[dict] = []
    for raw in db_paths:
        db = Path(raw)
        item: dict = {"db": str(db), "status": "skipped"}
        out.append(item)
        try:
            if not db.is_file():
                item["status"] = "missing"
                continue
            target_dir = Path(dest_dir) if dest_dir else default_backup_dir(db)
            if not force and not backup_due(target_dir, db.stem, interval_h):
                continue
            res = backup_sqlite(db, target_dir, tag="scheduled", keep=keep)
            check = verify_backup(res.path)
            item.update(status="backed_up" if check["ok"] else "backed_up_unverified", path=str(res.path),
                        journal=_str_or_none(res.journal_path), verified=check["ok"], problems=check["problems"])
            if not check["ok"]:
                logger.error("scheduled backup of {} failed its restore check: {}", db.name, check["problems"])
        except Exception as exc:  # noqa: BLE001
            item.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:300])
            logger.exception("scheduled backup of {} failed", db)
    return out


async def scheduled_backup_loop(db_paths, *, interval_h: float = 6.0, check_every_s: float = 600.0,
                                keep: int = KEEP_SCHEDULED, stop: asyncio.Event | None = None) -> None:
    """Background task for the web process: a pass every ``check_every_s``; copies are made when due."""
    while stop is None or not stop.is_set():
        paths = db_paths() if callable(db_paths) else db_paths
        await asyncio.to_thread(run_scheduled_backups, paths, interval_h=interval_h, keep=keep)
        try:
            if stop is None:
                await asyncio.sleep(check_every_s)
            else:
                await asyncio.wait_for(stop.wait(), timeout=check_every_s)
        except asyncio.TimeoutError:
            pass
