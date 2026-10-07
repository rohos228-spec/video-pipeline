"""Durable, explicitly scoped observation. No scheduling or recovery tools.

Outbox rows share the business session/transaction. Delivery to the master
journal is a separate transaction; there is no cross-database atomicity claim.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import functools
import json
import os
import re
import threading
import time
import uuid
from contextlib import asynccontextmanager, contextmanager, ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import JSON, Boolean, String, UniqueConstraint, event, inspect, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models import Base, NodeRun, Project, WorkflowRun
from app.services.llm_completion import response_complete, response_truncated
from app.services.observation_files import secure_directory


class ExecutionControl(Base):
    __tablename__ = "observation_executions"
    execution_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[int] = mapped_column(index=True)
    workflow_run_id: Mapped[int] = mapped_column(index=True)
    node_keys: Mapped[list] = mapped_column(JSON)
    graph_version: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(20), default="observe")
    observer_state: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    cursors: Mapped[dict] = mapped_column(JSON, default=dict)
    gaps: Mapped[list] = mapped_column(JSON, default=list)


class ObservationOutbox(Base):
    __tablename__ = "observation_outbox"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36), unique=True)
    execution_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[int] = mapped_column(index=True)
    workflow_run_id: Mapped[int] = mapped_column(index=True)
    node_key: Mapped[str | None] = mapped_column(String(60), default=None)
    attempt_id: Mapped[str | None] = mapped_column(String(36), default=None)
    request_id: Mapped[str | None] = mapped_column(String(36), default=None)
    db_scope: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class ObservationEvent(Base):
    __tablename__ = "observation_events"
    __table_args__ = (UniqueConstraint("execution_id", "event_id", name="uq_observation_delivery"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(36))
    execution_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[int] = mapped_column(index=True)
    workflow_run_id: Mapped[int] = mapped_column(index=True)
    node_key: Mapped[str | None] = mapped_column(String(60), default=None)
    attempt_id: Mapped[str | None] = mapped_column(String(36), default=None)
    request_id: Mapped[str | None] = mapped_column(String(36), default=None)
    db_scope: Mapped[str] = mapped_column(String(20))
    kind: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column()


class ObservationDegradation(Base):
    """History of observer degradations. Rows are closed, never deleted.

    execution_id is "" for a loop stage failure that concerns no single execution.
    """
    __tablename__ = "observation_degradations"
    id: Mapped[int] = mapped_column(primary_key=True)
    gap_id: Mapped[str] = mapped_column(String(36), unique=True)
    execution_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[int | None] = mapped_column(default=None)
    stage: Mapped[str] = mapped_column(String(40))
    error_type: Mapped[str] = mapped_column(String(120))
    recoverable: Mapped[bool] = mapped_column(Boolean, default=True)
    attempts: Mapped[int] = mapped_column(default=1)
    recorded_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    last_error_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    cleared_at: Mapped[datetime | None] = mapped_column(default=None)
    cleared_reason: Mapped[str | None] = mapped_column(String(200), default=None)


class CommitUncertaintyMarker(Base):
    """Business commit not confirmed. Open until a person clears it; survives restart."""
    __tablename__ = "observation_commit_uncertainty"
    id: Mapped[int] = mapped_column(primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[int | None] = mapped_column(default=None)
    reason: Mapped[str] = mapped_column(String(64))
    recorded_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    cleared_at: Mapped[datetime | None] = mapped_column(default=None)
    cleared_by: Mapped[str | None] = mapped_column(String(120), default=None)
    clear_reason: Mapped[str | None] = mapped_column(String(500), default=None)


from app.services.redact import (  # noqa: E402  (A16: единый redact)
    CREDENTIAL_FIELDS as _CREDENTIAL_FIELDS,
    _CREDENTIAL_TEXT,
    redact,
)


def digest(value) -> str:
    return hashlib.sha256(json.dumps(redact(value), ensure_ascii=False, sort_keys=True,
                                    default=str).encode()).hexdigest()


@dataclass
class AttemptContext:
    observer: Observer
    scope: ExecutionControl
    session: object
    db_scope: str
    node_key: str
    attempt_id: str
    last_request_id: str | None = None
    runtime_config_hash: str | None = None


_attempt = contextvars.ContextVar("observation_attempt", default=None)
# Project id of the native step in flight (set by advance_runner); used only as
# the cost-tracking fallback for calls made outside an observation scope.
_cost_project = contextvars.ContextVar("observation_cost_project", default=None)
_request = contextvars.ContextVar("observation_request", default=None)
_completed_model_request = contextvars.ContextVar("observation_completed_model_request", default=None)
_observer = None


def completed_model_request_id():
    """Task-local completion identity; shared attempt.last_request_id may race."""
    completed=_completed_model_request.get()
    return completed[1] if completed and completed[0] is _attempt.get() else _request.get()


@contextmanager
def bind_image_prompt_batch_request(batch_i):
    ctx=_attempt.get()
    request_id=(getattr(ctx,'image_prompt_batch_requests',{}) or {}).get(batch_i) if ctx else None
    token=_request.set(request_id)
    try:yield
    finally:_request.reset(token)


def auto_observe_enabled(project=None) -> bool:
    """C01: observation starts by itself for every project (no explicit observer.enable).

    Off switches: env OBSERVER_AUTO_ENABLE=0 (whole app) or project.meta["observer_auto"] = false (one project).
    The behaviour is controlled only by the setting / env; tests switch it off in tests/conftest.py.
    """
    raw = os.environ.get("OBSERVER_AUTO_ENABLE")
    if raw is not None and raw.strip().lower() in {"0", "false", "no", "off", ""}:
        return False
    meta = getattr(project, "meta", None)
    if isinstance(meta, dict) and meta.get("observer_auto") is False:
        return False
    return True


class Observer:
    MAX_BODY = 1024 * 1024
    MAX_TOTAL = 32 * 1024 * 1024  # per project (settings: observer_body_quota_bytes)
    RETENTION_SECONDS = 7 * 24 * 3600  # closed executions only (settings: observer_body_retention_seconds)
    LOOP_INTERVAL = 1
    CLEANUP_INTERVAL = 300
    QUOTA_EVENT_INTERVAL = 60
    # A recoverable gap is closed only after the consumer has been clean this long.
    RECOVERY_CLEAN_PASSES = 5
    RECOVERY_MIN_SECONDS = 15
    MAX_BACKOFF = 30

    def __init__(self, master_factory, *, body_root: Path, max_total=None, retention_seconds=None):
        self.master = master_factory
        self.body_root = Path(body_root)
        if max_total is not None:
            self.MAX_TOTAL = int(max_total)
        if retention_seconds is not None:
            self.RETENTION_SECONDS = int(retention_seconds)
        self.scopes: dict[int, ExecutionControl] = {}
        self._delivery_lock = asyncio.Lock()
        self._enable_lock = asyncio.Lock()
        self.failures: dict[str, str] = {}
        self.commit_uncertainty: dict[str, str] = {}
        self.last_project_facts: dict[tuple, str] = {}
        self._clock = time.monotonic
        # Failure state per execution: a failure of one never touches another.
        self._permanent: set[str] = set()  # pending failure that lost data: never auto-cleared
        self._retry: dict[str, tuple[int, float]] = {}
        self._clean: dict[str, tuple[int, float]] = {}
        self._stages: dict[str, dict] = {}
        self._execution_project: dict[str, int] = {}
        # Body store index: one scan per process, then maintained on write/purge.
        self._body_lock = threading.Lock()
        self._body_index: dict[str, dict] | None = None
        self._cleanup_requested = False
        self._cleanup_last = None
        self._quota_notified: dict[str, tuple[float, int]] = {}
        self._pending_events: list[tuple[str, str, dict]] = []
        self._events_lock = asyncio.Lock()
        self._uncertainty_unsaved: dict[str, str] = {}
        self._uncertainty_restored = False
        self._uncertainty_lock = asyncio.Lock()
        self._tasks: set = set()
        self._auto_lock = asyncio.Lock()
        self._auto_decided: set[tuple[int, int]] = set()
        self.auto_failures: dict[int, str] = {}

    @property
    def stage_errors(self):
        return {name: state["error_type"] for name, state in self._stages.items()}

    async def enable(self, *, project_id, workflow_run_id, node_keys, graph_version, initiator=None):
        if not node_keys or len(set(node_keys)) != len(node_keys):
            raise ValueError("Explicit, unique node scope required")
        async with self._enable_lock:
            await self.ensure_project_schema(project_id)
            async with self.master() as session:
                existing = (await session.scalars(select(ExecutionControl).where(
                    ExecutionControl.project_id == project_id,
                    ExecutionControl.observer_state.in_(["active", "degraded"]),
                ))).first()
                if existing:
                    raise ValueError("Observation already active for project")
                scope = ExecutionControl(execution_id=str(uuid.uuid4()), project_id=project_id,
                    workflow_run_id=workflow_run_id, node_keys=list(node_keys),
                    graph_version=str(graph_version), mode="observe", observer_state="active",
                    cursors={}, gaps=[])
                session.add(scope)
                if initiator:
                    self.stage(session, scope, "observer_control", db_scope="master",
                        payload={"action":"enabled", "initiator":initiator, "actor":"observer_api", "human_identity":None, "business_action":None})
                await session.commit()
            self.scopes[workflow_run_id] = scope
            self._execution_project[scope.execution_id] = project_id
            return scope

    async def ensure_auto_scope(self, project, *, force=False):
        """C01: an observe-mode scope over every node of the project's run, created at the first node start.

        Never raises and never blocks native work: a failure is logged and kept in ``auto_failures``.
        A scope that already exists (any state, including one the operator stopped) is never replaced,
        unless ``force`` (the operator switched auto-observation back on) and no active scope remains.
        """
        existing = next((s for s in self.scopes.values() if s.project_id == project.id), None)
        if existing is not None or not auto_observe_enabled(project):
            return existing
        async with self._auto_lock:
            existing = next((s for s in self.scopes.values() if s.project_id == project.id), None)
            if existing is not None:
                return existing
            try:
                async with self.master() as session:
                    run = (await session.scalars(select(WorkflowRun).where(
                        WorkflowRun.project_id == project.id).order_by(WorkflowRun.id.desc()))).first()
                    if run is None:
                        return None
                    decided = (project.id, run.id)
                    if decided in self._auto_decided and not force:
                        return None
                    known = (await session.scalars(select(ExecutionControl.execution_id).where(
                        ExecutionControl.project_id == project.id,
                        ExecutionControl.workflow_run_id == run.id))).first()
                    keys = [str(n.get("id")) for n in (run.nodes_snapshot or []) if n.get("id")]
                    version = revisions_graph_version(run.nodes_snapshot, run.edges_snapshot)
                    run_id = run.id
                if known is not None and not force:
                    self._auto_decided.add(decided)
                    return None
                keys = list(dict.fromkeys(keys))
                if not keys:
                    return None
                try:
                    scope = await self.enable(project_id=project.id, workflow_run_id=run_id, node_keys=keys,
                                              graph_version=version, initiator="auto_observe")
                except ValueError:
                    return None
                self._auto_decided.add(decided)
                self.auto_failures.pop(project.id, None)
                return scope
            except Exception as exc:  # noqa: BLE001 - observation must never stop the node
                self.auto_failures[project.id] = type(exc).__name__
                logger.warning("observation: auto-enable failed for project {}: {}", project.id, type(exc).__name__)
                return None

    async def ensure_project_schema(self, project_id):
        # Existing project.db files bypass init_project_db at application startup.
        # Migrate only the explicitly observed source before installing its scope.
        async with self.master() as session:
            project = await session.get(Project, project_id)
            directory = project.data_dir if project else None
        if directory and (Path(directory) / "project.db").exists():
            from app.project_db import get_project_engine
            engine = await get_project_engine(directory)
            async with engine.begin() as conn:
                from app.services.schema_migrations import SCOPE_PROJECT, migrate_orchestrator_schema
                await migrate_orchestrator_schema(conn, scope=SCOPE_PROJECT)

    async def reload(self):
        # Before any scope or diagnosis: an unconfirmed commit must outlive a restart.
        await self.restore_commit_uncertainty()
        async with self.master() as session:
            scopes = (await session.scalars(select(ExecutionControl).where(
                ExecutionControl.observer_state.in_(["active", "degraded"])
            ))).all()
        for scope in scopes:
            await self.ensure_project_schema(scope.project_id)
        self.scopes = {s.workflow_run_id: s for s in scopes}
        for scope in scopes:
            self._execution_project[scope.execution_id] = scope.project_id
        await asyncio.to_thread(self._warm_body_index)
        from app.services.actions_v1 import reload_permissions
        await reload_permissions(self.master)

    async def stop(self, execution_id, *, initiator=None):
        async with self.master() as session:
            scope = await session.get(ExecutionControl, execution_id)
            if scope is None:
                raise ValueError("Observation not found")
            if initiator and scope.observer_state != "stopped":
                self.stage(session, scope, "observer_control", db_scope="master",
                    payload={"action":"stopped", "initiator":initiator, "actor":"observer_api", "human_identity":None, "business_action":None})
            scope.observer_state = "stopped"
            await session.commit()
        cached = self.scopes.pop(scope.workflow_run_id, None)
        if cached:
            cached.observer_state = "stopped"

    def stage(self, session, scope, kind, *, db_scope, node_key=None, attempt_id=None,
              request_id=None, project_id=None, payload=None):
        """No commit here. A rollback removes this event with the business change."""
        if scope.mode not in {"observe", "managed"} or scope.observer_state not in {"active", "degraded"}:
            return False
        if project_id is not None and project_id != scope.project_id:
            return False
        if node_key is not None and node_key not in scope.node_keys:
            return False
        if db_scope not in {"master", "project"}:
            raise ValueError("Unknown database source")
        session.add(ObservationOutbox(event_id=str(uuid.uuid4()), execution_id=scope.execution_id,
            project_id=scope.project_id, workflow_run_id=scope.workflow_run_id, node_key=node_key,
            attempt_id=attempt_id, request_id=request_id, db_scope=db_scope,
            kind=kind, payload=redact(payload or {})))
        session.info.setdefault("observation_pending", set()).add(scope.execution_id)
        return True

    def pin(self, execution_id):
        folder = self.body_root / str(uuid.UUID(execution_id))
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        secure_directory(folder)
        (folder / ".pinned").touch(mode=0o600, exist_ok=True)

    def is_pinned(self, execution_id):
        return (self.body_root / str(uuid.UUID(execution_id)) / ".pinned").is_file()

    def save_body(self, execution_id, value):
        # UUID is validated before any path construction; callers cannot escape body_root.
        execution_id = str(uuid.UUID(execution_id))
        raw = json.dumps(redact(value), ensure_ascii=False, default=str).encode()
        if len(raw) > self.MAX_BODY:
            return {"gap": "body_size_limit", "bytes": len(raw)}
        folder = self.body_root / execution_id
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        secure_directory(folder)
        sha = hashlib.sha256(raw).hexdigest()
        path = folder / (sha + ".json")
        # Quota is per project and read from the in-memory index, not the disk.
        # Bodies are removed only by cleanup_bodies (closed, expired, unreferenced).
        blocked = None
        with self._body_lock:
            index = self._ensure_body_index()
            if not path.exists():
                project = self._project_of(execution_id)
                used = self._project_bytes(project)
                if used + len(raw) > self.MAX_TOTAL:
                    blocked = {"project_id": project, "bytes": len(raw), "project_bytes": used,
                               "limit": self.MAX_TOTAL}
                else:
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    entry = index.setdefault(execution_id, {"bytes": 0, "files": 0, "latest": 0.0, "purged": False})
                    entry["bytes"] += len(raw)
                    entry["files"] += 1
                    entry["latest"] = time.time()
        if blocked:
            self._body_quota_blocked(execution_id, blocked)
            return {"gap": "body_total_limit", "bytes": len(raw)}
        return {"path": path.relative_to(self.body_root).as_posix(), "sha256": sha, "bytes": len(raw)}

    def _warm_body_index(self):
        with self._body_lock:
            self._ensure_body_index()

    def _ensure_body_index(self):
        # Caller holds _body_lock.
        if self._body_index is None:
            index = {}
            if self.body_root.is_dir():
                for folder in os.scandir(self.body_root):
                    if not folder.is_dir():
                        continue
                    entry = index[folder.name] = {"bytes": 0, "files": 0, "latest": 0.0, "purged": False}
                    for item in os.scandir(folder.path):
                        if item.name == ".purged":
                            entry["purged"] = True
                        elif item.name.endswith(".json") and item.is_file():
                            stat = item.stat()
                            entry["bytes"] += stat.st_size
                            entry["files"] += 1
                            entry["latest"] = max(entry["latest"], stat.st_mtime)
            self._body_index = index
        return self._body_index

    def _project_of(self, execution_id):
        project = self._execution_project.get(execution_id)
        if project is None:
            project = next((s.project_id for s in self.scopes.values() if s.execution_id == execution_id), None)
            if project is not None:
                self._execution_project[execution_id] = project
        return project

    def _project_bytes(self, project):
        return sum(info["bytes"] for eid, info in self._body_index.items() if self._project_of(eid) == project)

    def body_usage(self):
        """Bytes of stored bodies per project (None = execution not known to this process)."""
        with self._body_lock:
            index = self._ensure_body_index()
            usage = {}
            for eid, info in index.items():
                key = self._project_of(eid)
                usage[key] = usage.get(key, 0) + info["bytes"]
            return usage

    def _body_quota_blocked(self, execution_id, details):
        self._cleanup_requested = True
        logger.warning("observer body quota blocked: execution={} project={} used={} limit={}",
                       execution_id, details["project_id"], details["project_bytes"], details["limit"])
        scope = next((s for s in self.scopes.values() if s.execution_id == execution_id), None)
        if scope is not None and scope.mode == "managed":
            # Mandatory audit body is missing: fail closed, never silently.
            self.mark_gap(execution_id, RuntimeError("body_total_limit"))
        last, suppressed = self._quota_notified.get(execution_id, (None, 0))
        now = self._clock()
        if last is not None and now - last < self.QUOTA_EVENT_INTERVAL:
            self._quota_notified[execution_id] = (last, suppressed + 1)
            return
        self._quota_notified[execution_id] = (now, 0)
        self._queue_event(execution_id, "body_quota_blocked", {**details, "suppressed_since_last": suppressed,
            "cleanup_scheduled": True, "business_action": None})

    def _queue_event(self, execution_id, kind, payload):
        self._pending_events.append((execution_id, kind, payload))

    async def flush_events(self):
        """Durable, visible journal events raised from synchronous code paths."""
        async with self._events_lock:
            batch = list(self._pending_events)
            if not batch:
                return 0
            async with self.master() as session:
                for execution_id, kind, payload in batch:
                    scope = await session.get(ExecutionControl, execution_id)
                    if scope is not None:
                        self._journal_event(session, scope, kind, payload)
                await session.commit()
            del self._pending_events[:len(batch)]
            return len(batch)

    @staticmethod
    def _journal_event(session, scope, kind, payload):
        """Observer-originated fact written straight to the master journal."""
        session.add(ObservationEvent(event_id=str(uuid.uuid4()), execution_id=scope.execution_id,
            project_id=scope.project_id, workflow_run_id=scope.workflow_run_id, node_key=None,
            attempt_id=None, request_id=None, db_scope="master", kind=kind,
            payload=redact(payload), created_at=datetime.utcnow()))

    async def cleanup_bodies(self, *, force=False):
        """Tombstone bodies of old, closed, unreferenced executions; report each as an event.

        Files stay (tiny tombstones with the original sha/size) so journal references
        do not turn into new body_not_available gaps. Live, pinned and referenced
        executions are never touched.
        """
        now = self._clock()
        due = self._cleanup_last is None or now - self._cleanup_last >= self.CLEANUP_INTERVAL
        if not (force or self._cleanup_requested or due):
            return []
        self._cleanup_last, self._cleanup_requested = now, False
        cutoff = time.time() - self.RETENTION_SECONDS
        with self._body_lock:
            index = self._ensure_body_index()
            candidates = [eid for eid, info in index.items()
                          if info["files"] and not info["purged"] and info["latest"] <= cutoff]
        candidates = [eid for eid in candidates if not self._is_pinned_safe(eid)]
        if not candidates:
            return []
        done = []
        async with self.master() as session:
            for eid in candidates:
                scope = await session.get(ExecutionControl, eid)
                if (scope is None or scope.observer_state != "stopped" or eid in self.commit_uncertainty
                        or await self._has_open_references(session, eid)):
                    continue
                before, after, files = self._purge_execution(eid)
                done.append(eid)
                self._queue_event(eid, "body_cleanup", {"reason": "retention", "retention_seconds": self.RETENTION_SECONDS,
                    "files": files, "bytes_before": before, "bytes_after": after, "business_action": None})
        if done:
            logger.info("observer body cleanup: {} closed executions", len(done))
            await self.flush_events()
        return done

    def _is_pinned_safe(self, execution_id):
        try:
            return self.is_pinned(execution_id)
        except ValueError:
            return True  # not an execution folder: leave it alone

    @staticmethod
    async def _has_open_references(session, execution_id):
        from app.services.actions_recovery import RecoveryAction
        from app.services.decision_state import DecisionRecord
        from app.services.diagnosis import Incident
        checks = (
            select(Incident.incident_id).where(Incident.execution_id == execution_id,
                Incident.status != "resolved_confirmed"),
            select(DecisionRecord.decision_id).where(DecisionRecord.execution_id == execution_id,
                DecisionRecord.status.in_(["ready", "waiting_evidence", "waiting_worker", "model_pending",
                                           "acting", "human_review"])),
            select(RecoveryAction.action_id).where(RecoveryAction.execution_id == execution_id,
                RecoveryAction.status.in_(["reserved", "queued", "claimed", "await_verify", "human_review"])),
        )
        for query in checks:
            if (await session.scalars(query.limit(1))).first() is not None:
                return True
        return False

    def _purge_execution(self, execution_id):
        folder = self.body_root / execution_id
        before = after = files = 0
        stamp = datetime.utcnow().isoformat()
        with self._body_lock:
            for item in sorted(folder.glob("*.json")):
                size = item.stat().st_size
                tomb = json.dumps({"purged": True, "sha256": item.stem, "bytes": size, "purged_at": stamp}).encode()
                temp = item.with_suffix(".tmp")
                temp.write_bytes(tomb)
                os.replace(temp, item)
                before, after, files = before + size, after + len(tomb), files + 1
            (folder / ".purged").touch(mode=0o600, exist_ok=True)
            entry = self._ensure_body_index().setdefault(execution_id,
                {"bytes": 0, "files": 0, "latest": 0.0, "purged": False})
            entry.update(bytes=after, files=files, purged=True, latest=time.time())
        return before, after, files

    async def consume(self, execution_id, db_scope, source_factory):
        """Cursor and journal entries commit together. Serialized in this one-process runtime."""
        async with self._delivery_lock:
            async with self.master() as dest:
                scope = await dest.get(ExecutionControl, execution_id)
                if scope is None:
                    return 0
                cursor = int((scope.cursors or {}).get(db_scope, 0))
                async with source_factory() as source:
                    rows = (await source.scalars(select(ObservationOutbox).where(
                        ObservationOutbox.execution_id == execution_id,
                        ObservationOutbox.db_scope == db_scope, ObservationOutbox.id > cursor,
                    ).order_by(ObservationOutbox.id).limit(500))).all()
                if not rows:
                    return 0
                delivered = 0
                for row in rows:
                    duplicate = (await dest.scalars(select(ObservationEvent.id).where(
                        ObservationEvent.execution_id == execution_id,
                        ObservationEvent.event_id == row.event_id,
                    ))).first()
                    if duplicate is None:
                        fields = {name: getattr(row, name) for name in (
                            "event_id", "execution_id", "project_id", "workflow_run_id", "node_key",
                            "attempt_id", "request_id", "db_scope", "kind", "payload", "created_at")}
                        dest.add(ObservationEvent(**fields))
                        delivered += 1
                scope.cursors = {**(scope.cursors or {}), db_scope: rows[-1].id}
                await dest.commit()
            # Notification follows durable delivery; missing notification is repaired by API history.
            from app.services.event_bus import get_bus
            await get_bus().publish(f"projects.{scope.project_id}", {
                "type": "observation_updated", "execution_id": execution_id,
            })
            return delivered

    def mark_gap(self, execution_id, error, *, recoverable=False):
        """Fail closed for this execution only.

        recoverable=True is for consumer-side unavailability (nothing was lost: the
        outbox is durable and replayed). Any other gap means evidence may be missing
        and is never cleared automatically.
        """
        self.failures[execution_id] = type(error).__name__
        if not recoverable:
            self._permanent.add(execution_id)
        cached = next((s for s in self.scopes.values() if s.execution_id == execution_id), None)
        if cached:
            cached.observer_state = "degraded"
        logger.warning("observer degraded: execution={} error_type={} recoverable={}",
                       execution_id, type(error).__name__, recoverable)

    def _backoff(self, attempts):
        return 0.0 if attempts <= 1 else min(2.0 ** (attempts - 2), self.MAX_BACKOFF)

    def _consumer_failed(self, execution_id, error):
        self.mark_gap(execution_id, error, recoverable=True)
        attempts = self._retry.get(execution_id, (0, 0.0))[0] + 1
        self._retry[execution_id] = (attempts, self._clock() + self._backoff(attempts))
        self._clean.pop(execution_id, None)

    async def catch_up(self):
        from app.project_db import get_project_sessionmaker
        # Stopped scopes may still have a transaction committing its recorded
        # tail. Delivery remains enabled; production hooks stay disabled.
        async with self.master() as session:
            deliveries = (await session.scalars(select(ExecutionControl))).all()
        for scope in deliveries:
            self._execution_project[scope.execution_id] = scope.project_id
            retry = self._retry.get(scope.execution_id)
            if retry and self._clock() < retry[1]:
                continue
            try:
                for _ in range(20):
                    if not await self.consume(scope.execution_id, "master", self.master):
                        break
                async with self.master() as session:
                    project = await session.get(Project, scope.project_id)
                    directory = project.data_dir if project else None
                if directory and (Path(directory) / "project.db").exists():
                    factory = await get_project_sessionmaker(directory)
                    for _ in range(20):
                        if not await self.consume(scope.execution_id, "project", factory):
                            break
                await self._settle(scope)
            except Exception as exc:
                self._consumer_failed(scope.execution_id, exc)

    async def _settle(self, scope):
        """After a fully successful pass: make a pending failure durable, or count towards recovery."""
        execution_id = scope.execution_id
        self._retry.pop(execution_id, None)
        if execution_id in self.failures:
            await self._record_gap(execution_id)
            return
        diagnoser = getattr(self, "diagnoser", None)
        if (scope.observer_state == "stopped" or not any(g.get("recoverable") for g in scope.gaps or [])
                or execution_id in (getattr(diagnoser, "failures", None) or {})):
            self._clean.pop(execution_id, None)
            return
        passes, since = self._clean.get(execution_id, (0, self._clock()))
        self._clean[execution_id] = (passes + 1, since)
        if passes + 1 >= self.RECOVERY_CLEAN_PASSES and self._clock() - since >= self.RECOVERY_MIN_SECONDS:
            await self._recover(execution_id)

    async def _record_gap(self, execution_id):
        error_type = self.failures[execution_id]
        permanent = execution_id in self._permanent
        now = datetime.utcnow()
        async with self.master() as session:
            stored = await session.get(ExecutionControl, execution_id)
            if stored.observer_state != "stopped":
                stored.observer_state = "degraded"
            gaps = list(stored.gaps or [])
            recoverable = not permanent and stored.observer_state != "stopped"
            open_gap = next((g for g in gaps if g.get("recoverable") and g.get("error_type") == error_type), None)
            if recoverable and open_gap:
                row = (await session.scalars(select(ObservationDegradation).where(
                    ObservationDegradation.gap_id == open_gap["gap_id"]))).first()
                if row is not None:
                    row.attempts += 1
                    row.last_error_at = now
            else:
                entry = {"error_type": error_type, "at": now.isoformat(), "detail": "Observer data may be incomplete"}
                if recoverable:
                    entry.update(gap_id=str(uuid.uuid4()), recoverable=True, stage="consumer")
                    session.add(ObservationDegradation(gap_id=entry["gap_id"], execution_id=execution_id,
                        project_id=stored.project_id, stage="consumer", error_type=error_type, recoverable=True,
                        attempts=1, recorded_at=now, last_error_at=now))
                    self._journal_event(session, stored, "observer_degraded", {"gap_id": entry["gap_id"],
                        "stage": "consumer", "error_type": error_type, "recoverable": True, "business_action": None})
                gaps.append(entry)
                stored.gaps = gaps
            await session.commit()
        self.failures.pop(execution_id, None)
        self._permanent.discard(execution_id)
        self._clean.pop(execution_id, None)

    async def _recover(self, execution_id):
        """Close recoverable gaps after the cause stayed gone. The fact stays in history."""
        now = datetime.utcnow()
        async with self.master() as session:
            stored = await session.get(ExecutionControl, execution_id)
            gaps = list(stored.gaps or [])
            closing = [g for g in gaps if g.get("recoverable")]
            if not closing or stored.observer_state == "stopped":
                return
            rest = [g for g in gaps if not g.get("recoverable")]
            reason = f"consumer_clean_{self.RECOVERY_CLEAN_PASSES}_passes"
            for gap in closing:
                row = (await session.scalars(select(ObservationDegradation).where(
                    ObservationDegradation.gap_id == gap["gap_id"]))).first()
                if row is not None:
                    row.cleared_at, row.cleared_reason = now, reason
            stored.gaps = rest
            if not rest and stored.observer_state == "degraded":
                stored.observer_state = "active"
            self._journal_event(session, stored, "observer_recovered", {
                "gap_ids": [g["gap_id"] for g in closing], "reason": reason,
                "remaining_gaps": len(rest), "business_action": None})
            state = stored.observer_state
            await session.commit()
        self._clean.pop(execution_id, None)
        cached = next((s for s in self.scopes.values() if s.execution_id == execution_id), None)
        if cached is not None:
            cached.gaps = rest
            if not rest and cached.observer_state == "degraded":
                cached.observer_state = state
        logger.info("observer recovered: execution={} remaining_gaps={}", execution_id, len(rest))

    async def _run_stage(self, name, call):
        """One loop stage. A failure here concerns no single execution, so it marks none."""
        state = self._stages.get(name)
        if state and self._clock() < state["next_at"]:
            return
        try:
            await call()
        except Exception as exc:
            attempts = (state["attempts"] if state else 0) + 1
            new = {"error_type": type(exc).__name__, "attempts": attempts,
                   "next_at": self._clock() + min(2.0 ** (attempts - 1), self.MAX_BACKOFF),
                   "gap_id": state["gap_id"] if state else str(uuid.uuid4())}
            self._stages[name] = new
            if attempts == 1 or attempts % 10 == 0:
                logger.warning("observer stage unavailable: stage={} error_type={} attempts={}",
                               name, type(exc).__name__, attempts)
            await self._stage_history(name, new, opened=state is None)
        else:
            if state:
                del self._stages[name]
                await self._stage_history(name, state, cleared=True)

    async def _stage_history(self, name, state, *, opened=False, cleared=False):
        try:
            async with self.master() as session:
                row = (await session.scalars(select(ObservationDegradation).where(
                    ObservationDegradation.gap_id == state["gap_id"]))).first()
                now = datetime.utcnow()
                if row is None and opened:
                    session.add(ObservationDegradation(gap_id=state["gap_id"], execution_id="", stage=name,
                        error_type=state["error_type"], recoverable=True, attempts=1,
                        recorded_at=now, last_error_at=now))
                elif row is not None and cleared:
                    row.cleared_at, row.cleared_reason = now, "stage_succeeded"
                elif row is not None:
                    row.attempts, row.last_error_at = state["attempts"], now
                await session.commit()
        except Exception:
            pass  # history is best effort; the stage state itself is kept in memory

    async def loop(self):
        loaded = False
        while True:
            if not loaded:
                await self._run_stage("reload", self.reload)
                loaded = "reload" not in self._stages
            if loaded:
                await self._run_stage("consumer", self.catch_up)
                await self._run_stage("diagnosis", self._diagnose)
                await self._run_stage("reconcile", self._reconcile)
                await self._run_stage("decision", self._decide)
                await self._run_stage("watchdog", self._watchdog)
                await self._run_stage("maintenance", self.maintain)
            await asyncio.sleep(self.LOOP_INTERVAL)

    async def _diagnose(self):
        from app.services.diagnosis import get_diagnoser
        await get_diagnoser(self).catch_up()

    async def _reconcile(self):
        from app.services.actions_recovery import reconcile
        await reconcile(self)

    async def _watchdog(self):
        from app.services.watchdog import get_watchdog
        await get_watchdog(self).tick()

    async def _decide(self):
        from app.services.decision_v1 import get_decider
        await get_decider(self).tick()

    async def maintain(self):
        await self.persist_uncertainty()
        await self.flush_events()
        await self.cleanup_bodies()

    def note_commit_uncertainty(self, execution_id, reason="business_commit_unconfirmed"):
        """Block now (memory), persist as soon as the event loop allows (database)."""
        self.commit_uncertainty[execution_id] = reason
        self._uncertainty_unsaved[execution_id] = reason
        try:
            task = asyncio.get_running_loop().create_task(self.persist_uncertainty())
        except RuntimeError:
            return  # no loop yet; maintain() retries
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def persist_uncertainty(self):
        async with self._uncertainty_lock:
            for execution_id, reason in list(self._uncertainty_unsaved.items()):
                try:
                    async with self.master() as session:
                        open_row = (await session.scalars(select(CommitUncertaintyMarker).where(
                            CommitUncertaintyMarker.execution_id == execution_id,
                            CommitUncertaintyMarker.cleared_at.is_(None)))).first()
                        if open_row is None:
                            scope = await session.get(ExecutionControl, execution_id)
                            session.add(CommitUncertaintyMarker(execution_id=execution_id, reason=reason,
                                project_id=scope.project_id if scope else None))
                            await session.commit()
                    self._uncertainty_unsaved.pop(execution_id, None)
                except Exception as exc:
                    logger.warning("commit uncertainty not yet persisted: execution={} error_type={}",
                                   execution_id, type(exc).__name__)

    async def restore_commit_uncertainty(self):
        """Unconfirmed commits survive a restart; they are closed only by a person."""
        async with self.master() as session:
            rows = (await session.scalars(select(CommitUncertaintyMarker).where(
                CommitUncertaintyMarker.cleared_at.is_(None)))).all()
        for row in rows:
            self.commit_uncertainty.setdefault(row.execution_id, row.reason)
        self._uncertainty_restored = True
        return len(rows)

    async def ensure_commit_uncertainty_restored(self):
        if not self._uncertainty_restored:
            await self.restore_commit_uncertainty()

    async def clear_commit_uncertainty(self, execution_id, *, initiator, reason):
        """Explicit, audited release. Returns False when nothing was open."""
        now = datetime.utcnow()
        async with self.master() as session:
            rows = (await session.scalars(select(CommitUncertaintyMarker).where(
                CommitUncertaintyMarker.execution_id == execution_id,
                CommitUncertaintyMarker.cleared_at.is_(None)))).all()
            scope = await session.get(ExecutionControl, execution_id)
            if not rows and execution_id not in self.commit_uncertainty:
                return False
            for row in rows:
                row.cleared_at, row.cleared_by, row.clear_reason = now, str(initiator)[:120], str(reason)[:500]
            if scope is not None:
                self._journal_event(session, scope, "observer_control", {"action": "commit_uncertainty_cleared",
                    "initiator": initiator, "actor": "observer_api", "reason": str(reason)[:500],
                    "human_identity": None, "business_action": None})
            await session.commit()
        self.commit_uncertainty.pop(execution_id, None)
        self._uncertainty_unsaved.pop(execution_id, None)
        return True


def revisions_graph_version(nodes, edges):
    from app.services import revisions
    return revisions.graph_version(nodes, edges)


def get_observer():
    global _observer
    if _observer is None:
        from app.db import SessionLocal
        from app.settings import settings
        _observer = Observer(SessionLocal, body_root=Path(settings.data_dir) / "observations" / "bodies",
            max_total=settings.observer_body_quota_bytes,
            retention_seconds=settings.observer_body_retention_seconds)
    return _observer


def emit(kind, payload=None, *, request_id=None, body=None):
    """Stage a scoped runtime fact; caller's transaction still decides visibility."""
    ctx = _attempt.get()
    if ctx is None or ctx.scope.observer_state == "stopped":
        return
    try:
        payload = dict(payload or {})
        if body is not None:
            payload["body"] = ctx.observer.save_body(ctx.scope.execution_id, body)
        ctx.observer.stage(ctx.session, ctx.scope, kind, db_scope=ctx.db_scope,
            node_key=ctx.node_key, attempt_id=ctx.attempt_id, request_id=request_id, payload=payload)
    except Exception as exc:
        ctx.observer.mark_gap(ctx.scope.execution_id, exc)


async def record_fact(kind, payload=None, *, request_id=None, body=None):
    """External/runtime facts survive project rollback; source is master, explicitly.

    These facts never claim project data was committed. Own journal writes are
    excluded from the business-row hook.
    """
    ctx = _attempt.get()
    if ctx is None or ctx.scope.observer_state == "stopped":
        if ctx is None:
            from app.services.decision_transport import fact
            await fact(kind, payload, body=body, request_id=request_id)
        return
    if ctx.db_scope == "master":
        # Fallback execution shares the master business transaction; a second
        # writer would deadlock. Its events have the same rollback semantics.
        emit(kind, payload, request_id=request_id or _request.get(), body=body)
        return
    try:
        details = dict(payload or {})
        if body is not None:
            details["body"] = ctx.observer.save_body(ctx.scope.execution_id, body)
        async with ctx.observer.master() as session:
            ctx.observer.stage(session, ctx.scope, kind, db_scope="master", node_key=ctx.node_key,
                attempt_id=ctx.attempt_id, request_id=request_id or _request.get(), payload=details)
            await session.commit()
        if isinstance(details.get('body'),dict) and details['body'].get('gap'):
            ctx.observer.mark_gap(ctx.scope.execution_id,RuntimeError('mandatory body unavailable'))
    except Exception as exc:
        ctx.observer.mark_gap(ctx.scope.execution_id, exc)


async def hold_uncertain_request(ctx,request_id):
    from app.services.actions_v1 import ActionSupervisor
    await ActionSupervisor(ctx.observer.master).set_held(project_id=ctx.scope.project_id,
        execution_id=ctx.scope.execution_id,held=True,initiator='http_policy',
        rationale='Неопределённый внешний ответ: повторный POST запрещён до разбора человеком',
        idempotency_key='uncertain-http:'+request_id)


async def authorize_model_retrieve(url):
    """Retrieval GET shares the same durable request limit, never a POST retry."""
    ctx=_attempt.get()
    if ctx is None:
        from app.services.decision_transport import current
        hook=current.get()
        if hook is not None:await hook.retrieve(url)
        return
    if ctx.scope.mode!='managed':return
    from app.services.actions_v1 import ActionSupervisor
    await record_fact('model_retrieve_start',{'method':'GET','purpose':'node','url':url})
    await ActionSupervisor(ctx.observer.master).charge_request(ctx.scope.execution_id)


def observe_review(function):
    """Bind native text reviews to the existing scope, without creating an attempt.

    Unknown/ambiguous review ownership fails closed in managed mode. Unmanaged
    projects retain the native behavior. This is accounting, not a new executor.
    """
    import inspect
    signature=inspect.signature(function)
    @functools.wraps(function)
    async def wrapped(*args,**kwargs):
        bound=signature.bind(*args,**kwargs).arguments
        project=bound['project']
        from app.services.actions_v1 import managed_project_enabled,ActionExecutionControl
        if not await managed_project_enabled(project.id):
            return await function(*args,**kwargs)
        observer=get_observer()
        kind=bound.get('kind',bound.get('step_code'))
        name=str(getattr(kind,'value',kind))
        node_type={'approve_plan':'plan','approve_script':'script','plan':'plan','script':'script'}.get(name)
        from app.models import NodeRun
        async with observer.master() as master:
            scopes=(await master.scalars(select(ExecutionControl).where(
                ExecutionControl.project_id==project.id,ExecutionControl.mode=='managed',
                ExecutionControl.observer_state=='active'))).all()
            if len(scopes)!=1 or node_type is None:
                raise RuntimeError('managed review scope unavailable')
            scope=scopes[0]
            permission=await master.get(ActionExecutionControl,scope.execution_id)
            if permission is None or not permission.enabled:
                raise RuntimeError('managed review scope permission unavailable')
            keys=(await master.scalars(select(NodeRun.node_key).where(
                NodeRun.workflow_run_id==scope.workflow_run_id,NodeRun.node_type==node_type,
                NodeRun.node_key.in_(set(scope.node_keys)&set(permission.node_keys))))).all()
            if len(keys)!=1:raise RuntimeError('managed review outside scope or ambiguous node')
        ctx=AttemptContext(observer,scope,None,'project',keys[0],'review-'+str(uuid.uuid4()))
        ctx.purpose='review'
        token=_attempt.set(ctx)
        try:return await function(*args,**kwargs)
        finally:_attempt.reset(token)
    return wrapped


def _usage_tokens(usage):
    """Normalize a provider usage dict to (prompt, completion, cached) token
    counts. Handles both Responses-API naming (input_tokens/output_tokens,
    the shape this app's Kie endpoint actually returns -- see
    web/src/lib/observation-view.ts) and Chat-Completions naming
    (prompt_tokens/completion_tokens), since api_tracker_hook.record_api_call
    was written against the latter.
    """
    usage = usage if isinstance(usage, dict) else {}
    prompt = usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0
    completion = usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0
    cached = usage.get("cached_tokens", 0) or 0
    details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details")
    if isinstance(details, dict):
        cached = details.get("cached_tokens", cached) or cached
    if not prompt:
        # Kie's /codex/v1/responses reports input_tokens=0 at the top level
        # (observed on a real call, 2026-10-01) and puts the real per-part
        # figures under usage.attribution.{request_fields,items}.*; summing
        # each part's own totals (not their nested "content" breakdowns, which
        # would double count) recovers the true input and cached counts.
        attribution = usage.get("attribution")
        if isinstance(attribution, dict):
            parts = []
            for group in ("request_fields", "items"):
                section = attribution.get(group)
                if isinstance(section, dict):
                    parts.extend(v for v in section.values() if isinstance(v, dict))
            prompt = sum(int(p.get("input_tokens") or 0) for p in parts)
            attributed_cached = sum(int(p.get("cached_tokens") or 0) for p in parts)
            cached = cached or attributed_cached
            if not completion:
                completion = sum(int(p.get("output_tokens") or 0) for p in parts)
    return int(prompt), int(completion), int(cached)


# Errors after which the provider may already have accepted (and billed) the request.
_UNCERTAIN_ERROR_TYPES = frozenset({"CancelledError", "ReadTimeout", "WriteTimeout", "ReadError",
                                    "RemoteProtocolError", "TimeoutError"})


def effective_text_provider() -> str:
    """Provider the current text request really goes to: node override first, then the
    header setting. Mirrors gpt_api.is_responses_mode; a profile never changes it."""
    try:
        from app.services.llm_override import current_override
        ov = current_override()
        if ov is not None and ov.kind == "text" and ov.provider in {"kie", "vibecode"}:
            return ov.provider
        from app.settings import settings
        return "vibecode" if settings.text_llm_is_vibecode else "kie"
    except Exception:
        return "unknown"


def _track_api_cost(*, model, usage, duration_sec, project_id=None,
                     status_code=200, error_message="", provider: str = "kie"):
    """Fire-and-forget cost tracking via the existing api-tracker module --
    never recomputes pricing here (api_tracker_hook._calc_cost owns that),
    never blocks the caller (record_api_call spawns its own daemon thread),
    never raises (api_tracker_hook already fails safe internally, but this
    call site is additionally guarded since cost tracking must never be
    able to break a real model request).

    Not gated by a ``synthetic*`` key and not by pytest (A08): such a key used to switch recording off
    silently. Now a call is skipped only when the provider has no credential at all (nothing could have been
    billed) or MEDIA_SPEND_RECORDING is false; a ``synthetic*`` key only tags the row (``synthetic_key``
    in the tracker metadata) so it can be told apart. The local spend ledger is written only when it is the
    configured database (``media_spend.ledger_target_ok``), so a sandbox never writes into the real one.
    Cloud sending is separate and off by default (API_TRACKER_CLOUD_ENABLED, A17).

    The gate is the key of the provider that took the request (Kie: GPT_API_KEY,
    Vibecode: VIBECODE_API_KEY); one provider's key never enables the other's row.
    Vibecode rows are priced from the catalog snapshot, not Kie's calibrated rates
    (see api_tracker_hook.VIBECODE_PRICE_SOURCE). Every row carries ``cost_kind=estimated``:
    the price is computed here, not reported by the provider.

    A failure is never silent: logged with its traceback and traced as a gap (media_spend.note_gap).
    """
    try:
        if project_id is None:
            project_id = _cost_project.get()
        from app.settings import settings
        provider = str(provider or "kie").lower()
        if provider == "kie":
            key = str(settings.gpt_api_key or "").strip()
        elif provider == "vibecode":
            key = str(settings.vibecode_api_key or "").strip()
        else:
            return
        if not key or not settings.media_spend_recording:
            return
        from app.services import api_tracker_hook, media_spend
        prompt_tokens, completion_tokens, cached_tokens = _usage_tokens(usage)
        extra = {}
        if provider == "vibecode":
            extra["metadata"] = api_tracker_hook.vibecode_cost_metadata(
                model, usage_known=bool(prompt_tokens or completion_tokens))
        meta = dict(extra.get("metadata") or {})
        meta["cost_kind"] = "estimated"
        if key.startswith("synthetic"):
            meta["synthetic_key"] = True
        extra["metadata"] = meta
        if status_code == 200:  # local spend ledger: settle the reservation / record a native call
            if media_spend.TEST_FORCE or media_spend.ledger_target_ok():
                from app.services import spend_guard
                spend_guard.schedule_actual(project_id=project_id, provider=provider, usd=api_tracker_hook._calc_cost(
                    str(model or "unknown"), prompt_tokens, completion_tokens, "text", duration_sec,
                    cached_tok=cached_tokens, provider=provider))
            else:
                media_spend._count_skip(provider, "ledger_target_mismatch")
        api_tracker_hook.record_api_call(
            provider=provider, model=str(model or "unknown"), call_type="text",
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            cached_tokens=cached_tokens, duration_sec=duration_sec,
            status_code=status_code, error_message=str(redact(str(error_message or "")))[:500],
            project_source=str(project_id) if project_id is not None else "", **extra,
        )
    except Exception as exc:
        # Cost tracking must never affect the real request, but a lost row must leave a trace.
        try:
            from app.services import media_spend as _ms
            _ms.note_gap(str(provider or ""), "track_api_cost", project_id=project_id, error=exc,
                         detail=str(model or ""))
        except Exception:
            logger.opt(exception=True).error("observation: cost tracking failed and the gap was not traced")


async def _credit_guard(exc, provider, master=None):
    """HTTP 402 / credits exhausted -> engage the supervisory kill switch (spend_guard)."""
    try:
        if master is None:  # unmanaged call: only with a real credential, never from synthetic/test runs
            from app.settings import settings
            key = str(settings.vibecode_api_key if provider == "vibecode" else settings.gpt_api_key or "").strip()
            if not key or key.startswith("synthetic"):
                return
        from app.services import spend_guard
        await spend_guard.engage_on_credit_error(exc, master=master, provider=provider)
    except Exception:
        pass


def observe_model(function):
    """One request identity for each low-level POST, including retry/split calls."""
    @functools.wraps(function)
    async def wrapped(*args, **kwargs):
        ctx = _attempt.get()
        if ctx is None:
            from app.services.decision_transport import current
            hook = current.get()
            if hook is not None:
                return await hook.send(function, args, kwargs)
            start = time.monotonic()
            provider = effective_text_provider()
            try:
                result = await function(*args, **kwargs)
            except Exception as exc:
                _track_api_cost(model=kwargs.get("model"), usage=None,
                    duration_sec=time.monotonic() - start, provider=provider,
                    status_code=0, error_message=f"{type(exc).__name__}: {exc}")
                await _credit_guard(exc, provider)
                raise
            _track_api_cost(model=getattr(result, "model", kwargs.get("model")),
                usage=getattr(result, "usage", None), duration_sec=time.monotonic() - start,
                provider=provider)
            return result
        request_id = str(uuid.uuid4())
        previous_request_id = ctx.last_request_id
        from app.services.profiles_v1 import transform_request
        kwargs = await transform_request(ctx, kwargs)
        body = kwargs.get("body") or {}
        provider = effective_text_provider()
        if ctx.scope.mode == "managed":
            from app.services.actions_recovery import check_body
            await check_body(ctx, digest(body), body=body)
        ctx.last_request_id = request_id
        request_token = _request.set(request_id)
        from urllib.parse import urlsplit
        endpoint = urlsplit(str(kwargs.get("url") or ""))
        settings = {k: v for k, v in body.items() if k not in {"input", "messages", "instructions"}}
        if ctx.scope.mode == "managed":
            try:
                from app.services.actions_v1 import ActionSupervisor
                await ActionSupervisor(ctx.observer.master).charge_request(ctx.scope.execution_id)
            except Exception as exc:
                await record_fact("action_blocked", {
                    "action": "model_request", "reason": type(exc).__name__,
                    "request_id": request_id,
                }, request_id=request_id)
                _request.reset(request_token)
                raise
        await record_fact("model_start", {"method": "POST", "host": endpoint.hostname,
            "path": endpoint.path, "model": kwargs.get("use_model"), "provider": provider,
            "purpose":getattr(ctx,'purpose','node'),
            "previous_request_id": previous_request_id,
            "settings_revision": digest(settings), "input_revision": digest(body)}, body=body)
        start = time.monotonic()
        call_attempted = False
        cost_tracked = False
        try:
            if ctx.scope.mode=='managed' and (ctx.scope.gaps or ctx.scope.execution_id in ctx.observer.failures or ctx.scope.execution_id in ctx.observer.commit_uncertainty):
                raise RuntimeError('managed request blocked before transport: mandatory audit unavailable')
            call_attempted = True
            result = await function(*args, **kwargs)
            raw = getattr(result, "raw", {}) or {}
            finish_reason = getattr(result, "finish_reason", None)
            await record_fact("model_end", {"model": getattr(result, "model", None), "provider": provider,
                "response_id": getattr(result, "response_id", None),
                "finish_reason": finish_reason,
                "usage": getattr(result, "usage", None),
                "complete": response_complete(raw, finish_reason),
                "retrieved_by_id": bool(raw.get("retrieved_by_id")),
            }, body={"text": getattr(result, "text", ""), "response": raw})
            _track_api_cost(model=getattr(result, "model", None), usage=getattr(result, "usage", None),
                duration_sec=time.monotonic() - start, project_id=getattr(ctx.scope, "project_id", None),
                provider=provider)
            cost_tracked = True
            # Responses: a status other than completed; chat/completions (no status): a cut-off finish_reason.
            if ctx.scope.mode=='managed' and (raw.get('sse_salvaged') or raw.get('status') not in {None,'completed'}
                    or response_truncated(raw, finish_reason)):
                await hold_uncertain_request(ctx,request_id)
                raise RuntimeError('uncertain external result; managed execution held, no repeat POST')
            _completed_model_request.set((ctx,request_id))
            return result
        except BaseException as exc:
            from app.services.error_catalog import describe_error
            context = getattr(exc,"context",None) or {}
            await record_fact("model_error", {"error_type": type(exc).__name__, "complete": False,
                "provider": provider, "model": kwargs.get("use_model"),
                "error_code":describe_error(exc)[0],"error_kind":context.get("error_kind") if isinstance(context,dict) else None,
                "catalog_source":"structured_exception_context" if isinstance(context,dict) and context.get("error_kind") else "exception_type_or_text",
                "status_code":context.get("status_code") if isinstance(context,dict) else None,
                "uncertain":type(exc).__name__ in _UNCERTAIN_ERROR_TYPES or (isinstance(context,dict) and (
                    context.get("error_kind") in {"timeout","network"} or context.get("stream_error") in _UNCERTAIN_ERROR_TYPES))})
            # Only track a failed-call cost entry for a request that actually
            # reached the provider and wasn't already tracked above (the
            # "uncertain external result" re-raise follows a real, already-
            # tracked success -- tracking it again would double-count spend).
            if call_attempted and isinstance(exc, Exception):
                await _credit_guard(exc, provider, ctx.observer.master)
            if call_attempted and not cost_tracked:
                _track_api_cost(model=kwargs.get("use_model"), usage=None, provider=provider,
                    duration_sec=time.monotonic() - start, project_id=getattr(ctx.scope, "project_id", None),
                    status_code=(context.get("status_code") if isinstance(context,dict) else None) or 0,
                    error_message=f"{type(exc).__name__}: {exc}")
            if ctx.scope.mode=='managed' and (type(exc).__name__ in _UNCERTAIN_ERROR_TYPES|{'ConnectTimeout'} or (isinstance(context,dict) and (
                    context.get('error_kind') in {'timeout','network'} or context.get('stream_error') in _UNCERTAIN_ERROR_TYPES))):
                await hold_uncertain_request(ctx,request_id)
            raise
        finally:
            _request.reset(request_token)
    return wrapped


async def record_parse(text, parsed, *, payload_required=True,
                       empty_ops_allowed=False, empty_ops_accepted=False):
    try:
        strict = json.loads(text)
        syntax_valid = True
    except (ValueError, TypeError):
        strict = None
        syntax_valid = False
    fields = strict if isinstance(strict, dict) else parsed
    ctx = _attempt.get()
    await record_fact("parse_result", {"syntax_valid": syntax_valid,
        "parsed": parsed is not None, "schema_valid": (True if empty_ops_accepted else
            False if empty_ops_allowed and syntax_valid and parsed is None else None),
        "contract": "json_apply_ops" if payload_required else "text_verdict_or_optional_apply_ops",
        "schema_note": "Extraction does not prove node schema validation",
        "payload_required": payload_required,
        "nonempty_payload_required": payload_required and not empty_ops_allowed,
        "ops_field_present": isinstance(fields, dict) and 'ops' in fields,
        "ops_type_valid": isinstance(fields, dict) and isinstance(fields.get('ops'), list),
        "empty_ops_allowed": empty_ops_allowed,
        "empty_ops_accepted": empty_ops_accepted,
        "semantic_quality_verified": False,
        "payload_present": bool(parsed and any(parsed.get(k) for k in ("ops", "characters", "scenes", "actions"))),
        "ops_count": len(fields['ops']) if isinstance(fields, dict) and isinstance(fields.get('ops'), list) else None}, body={"text": text, "parsed": parsed},
        request_id=ctx.last_request_id if ctx else None)


def observe_apply(function):
    @functools.wraps(function)
    async def wrapped(session, project, *args, **kwargs):
        if _attempt.get() is None:
            return await function(session, project, *args, **kwargs)
        source_request=_request.get() or completed_model_request_id() or _attempt.get().last_request_id
        emit("apply_input", {"node_kind": kwargs.get("node_kind")}, body={"ops": args[0] if args else kwargs.get("ops"),
            "characters": kwargs.get("characters"), "scenes": kwargs.get("scenes")},request_id=source_request)
        try:
            result = await function(session, project, *args, **kwargs)
        except BaseException as exc:
            await record_fact("apply_error", {"error_type": type(exc).__name__})
            raise
        emit("data_applied", {"result": result, "transactional": True},request_id=source_request)
        emit("runtime_check", {"check": "apply_ops_validation", "status": "passed",
            "performed": True, "result": result, "initiator": "worker",
            "business_commit_claim": False},request_id=source_request)
        return result
    return wrapped


def _media_resume_nodes():
    """Nodes whose attempt records a request-input baseline (paid media resumes and local supervised re-runs)."""
    from app.services.revisions import BASELINE_NODES
    return BASELINE_NODES


@asynccontextmanager
async def observe_attempt(session, project):
    observer = get_observer()
    await observer.ensure_auto_scope(project)
    from app.services.excel_gpt_node import active_excel_gpt_node_key
    node_key = active_excel_gpt_node_key(project)
    # Native media (and other registry nodes) do not use the excel active key.
    # Resolve from the immutable run and actual running NodeRun, never a guessed
    # first node of a repeated type or a stale key left by a previous text step.
    from app.orchestrator.node_registry import RUNNING_TO_NODE_TYPE, spec_for_node
    native_type = RUNNING_TO_NODE_TYPE.get(project.status)
    project_scope = next((s for s in observer.scopes.values() if s.project_id == project.id), None)
    if project_scope and native_type and not native_type.startswith("enrich_"):
        from app.models import NodeRunStatus
        async with observer.master() as master:
            run = await master.get(WorkflowRun, project_scope.workflow_run_id)
            candidates = {n["id"] for n in (run.nodes_snapshot if run else [])
                          if spec_for_node(n) and spec_for_node(n).running_status == project.status}
            active = (await master.scalars(select(NodeRun).where(
                NodeRun.workflow_run_id == project_scope.workflow_run_id,
                NodeRun.node_key.in_(candidates), NodeRun.status == NodeRunStatus.running))).all()
        keys = [n.node_key for n in active] if active else list(candidates)
        if len(keys) != 1:
            if project_scope.mode == "managed":
                raise RuntimeError("managed native node identity missing or ambiguous")
            observer.mark_gap(project_scope.execution_id, ValueError("native node identity ambiguous"))
            node_key = None
        else:
            node_key = keys[0]
            if project_scope.mode == "managed" and node_key not in project_scope.node_keys:
                raise RuntimeError("native worker node outside managed scope")
    scope = next((s for s in observer.scopes.values()
                  if s.project_id == project.id and node_key in s.node_keys), None)
    if scope is None:
        # Stopping observation must not implicitly remove a managed execution's
        # request-budget gate. Rehydrate the durable scope for accounting only;
        # its stopped observer state still prevents new journal writes.
        from app.services.actions_v1 import get_permission
        permission = get_permission(project.id)
        if permission is not None and node_key in permission.node_keys:
            async with observer.master() as master_session:
                scope = await master_session.get(ExecutionControl, permission.execution_id)
    if scope is None:
        yield
        return
    source = "project" if Path(str(session.bind.url.database)).name == "project.db" else "master"
    attempt_id = str(uuid.uuid4())
    revision = None
    revision_parts = {}
    recovery_claimed = False
    if scope.mode == "managed":
        from app.services.actions_recovery import claim, input_revision
        claimed_id = await claim(observer, session, project, node_key, attempt_id, execution_id=scope.execution_id)
        if claimed_id != attempt_id:
            session.info["recovery_attempt_id"] = claimed_id
            recovery_claimed = True
        attempt_id = claimed_id
        revision = await input_revision(session, project, node_key, parts=revision_parts)
    elif native_type in _media_resume_nodes() and node_key:
        # Media nodes and the local assemble are observed without a managed scope; the supervised recovery
        # (media_admission / local_node_recovery) is admitted later, so the input baseline of THIS attempt
        # (the request inputs of a paid task, or the true inputs of the local work) is recorded now. Never fatal.
        try:
            from app.services.actions_recovery import input_revision
            revision = await input_revision(session, project, node_key, parts=revision_parts)
        except Exception:  # noqa: BLE001 - a missing baseline only makes the later admission refuse
            revision, revision_parts = None, {}
    ctx = AttemptContext(observer, scope, session, source, node_key, attempt_id)
    if scope.mode == "managed":
        from app.services.decision_config import runtime_config
        ctx.runtime_config_hash = digest(runtime_config())
    token = _attempt.set(ctx)
    from app.services.node_capabilities import capability_for
    capability = capability_for("excel_gpt" if not native_type or native_type.startswith("enrich_") else native_type)
    ctx.native_node_type = capability.node_type
    # A local node (assemble) claimed by a recovery ticket runs inside the resume-only scope: any paid seam refuses
    # before a byte leaves and is counted (local_recovery_guard fact, read back by the ticket's verification).
    from app.orchestrator.node_spec import capability_spec_for
    node_spec_data = capability_spec_for(capability.node_type)
    local_recovery = bool(recovery_claimed and node_spec_data is not None and node_spec_data.local_recovery)
    guard_hits: list = []

    async def record_guard():
        if local_recovery:
            await record_fact("local_recovery_guard", {"resume_only": True, "paid_seam_hits": len(guard_hits),
                                                       "hits": guard_hits[:5], "node_type": capability.node_type})

    await record_fact("node_start", {"project_status": project.status.value, "node_contract": node_key,
                                  "result_kind": capability.result_kind,
                                  "recovery_input_revision": revision, "recovery_revision_parts": revision_parts})
    profile_stack = ExitStack()
    try:
        if scope.mode == "managed":
            from app.services.node_capabilities import require_managed_capability
            try:
                require_managed_capability(capability.node_type, recovery_claim=local_recovery)
            except ValueError as exc:
                from app.services.media_jobs import _hold
                await _hold(ctx, str(exc), "native-capability:"+node_key)
                raise
        if local_recovery:
            from app.services import media_spend
            profile_stack.enter_context(media_spend.resume_only(guard_hits))
        from app.services.profiles_v1 import load_attempt, bind_attempt
        overlay = await load_attempt(ctx)
        profile_stack.enter_context(bind_attempt(overlay))
        if overlay:
            await record_fact("profile_effective_context", {k:overlay[k] for k in ("overlay_id","profile_id","candidate_hash","base_version","attempt_id","candidate")})
        yield
        # Node-type result hooks come from the named verifier registry
        # (node_spec.verifier_spec_for), not per-type branches.
        from app.orchestrator.node_spec import run_verifier_after_success
        await run_verifier_after_success(ctx, session, project, capability.node_type)
        await observe_only_result_check(ctx, session, project, capability.node_type)
        await record_guard()
        if getattr(ctx, 'scene_native_node', None) is not None:
            try:
                from app.services.scene_contracts import observe_scene_result
                await observe_scene_result(session, project, ctx.scene_native_node,
                    agent_slice=getattr(ctx, 'scene_agent_slice', None),
                    payload=getattr(ctx, 'scene_assembly_payload', None))
                ctx.scene_result_observed = True
            except Exception as snapshot_error:
                observer.mark_gap(scope.execution_id, snapshot_error)
    except BaseException as exc:
        from app.orchestrator.node_spec import run_verifier_after_error
        from app.services.step_cancel import StepCancelledError
        if isinstance(exc, Exception) and not isinstance(exc, StepCancelledError):
            # The failed attempt's OWN outcome, for the result hook: a file an earlier run left behind is not
            # the result of this attempt (media_verification types the failure by this, not by that file).
            ctx.attempt_exception = exc
        await run_verifier_after_error(ctx, session, project, capability.node_type, exc)
        if isinstance(exc, Exception):
            try:
                await record_guard()
            except Exception:  # noqa: BLE001 - the original failure is what propagates
                pass
        if (isinstance(exc, Exception) and getattr(ctx, 'scene_native_node', None) is not None
                and not getattr(ctx, 'scene_result_observed', False)):
            try:
                from app.services.scene_contracts import observe_scene_result
                await observe_scene_result(session, project, ctx.scene_native_node,
                    agent_slice=getattr(ctx, 'scene_agent_slice', None),
                    payload=getattr(ctx, 'scene_assembly_payload', None))
            except Exception as snapshot_error:
                observer.mark_gap(scope.execution_id, snapshot_error)
        from app.services.profiles_v1 import finish_attempt
        await finish_attempt(ctx, True)
        from app.services.apply_ops_batches import pass_stop_rule
        rule = pass_stop_rule(exc)
        await record_fact("node_error", {"error_type": type(exc).__name__, **({"rule": rule} if rule else {})})
        if session.info.get("recovery_attempt_id") == attempt_id:
            from app.services.actions_recovery import worker_finished
            await worker_finished(ctx, error=True)
        raise
    else:
        from app.services.profiles_v1 import finish_attempt
        await finish_attempt(ctx, False)
        await record_fact("node_end", {"project_status": project.status.value,
                          "next_node_key": active_excel_gpt_node_key(project), "worker_returned": True,
                          "business_commit_claim": False})
        if session.info.get("recovery_attempt_id") == attempt_id:
            from app.services.actions_recovery import worker_finished
            await worker_finished(ctx)
    finally:
        ctx.native_attempt_closed = True
        profile_stack.close()
        _attempt.reset(token)
        session.info.pop("recovery_attempt_id", None)


OBSERVE_ONLY_CHECK_TYPES = frozenset({"plan", "script", "split", "image_prompts"})


async def observe_only_result_check(ctx, session, project, node_type):
    """C01/C02: the result check of plan/script/split/image_prompts outside a managed scope.

    The native verifiers hold or fail the run in managed mode only. Here the same pure proofs are journalled with
    ``observe_only`` and an incident (runtime_check failed) is raised on a broken result, but the run is never held,
    retried or failed: no raise, no recovery right. Returns the proof dict, or None when nothing was checked.
    """
    if ctx.scope.mode == "managed" or node_type not in OBSERVE_ONLY_CHECK_TYPES:
        return None
    try:
        from app.services import node_capabilities as caps
        if node_type in {"plan", "script"}:
            proof = caps.verify_project_text(project, node_type)
            body = {proof["field"]: getattr(project, proof["field"], None)}
        else:
            from sqlalchemy.ext.asyncio import async_sessionmaker
            from app.models import Frame
            async with async_sessionmaker(session.bind, expire_on_commit=False, autoflush=False)() as reader:
                frames = (await reader.scalars(select(Frame).where(Frame.project_id == project.id)
                                               .order_by(Frame.number))).all()
            if node_type == "split":
                proof = caps.verify_split_frames(frames, project_id=project.id,
                                                 voiceover=getattr(ctx, "split_input_voiceover", None))
                body = {"frames": [{"uuid": f.uuid, "number": f.number, "voiceover_text": f.voiceover_text}
                                   for f in frames]}
            else:
                proof = caps.verify_image_prompts(frames, project_id=project.id,
                                                  snapshot=getattr(ctx, "image_prompt_input", None))
                body = {"frame_count": len(frames), "frame_set_sha256": proof.get("frame_set_sha256")}
        proof = {**proof, "observe_only": True, "run_held": False}
        await record_fact("node_result_verification", proof, body=body)
        if proof.get("status") == "failed" and node_type != "image_prompts":
            # image_prompts already becomes a verification_hold incident from the verification fact itself.
            await record_fact("runtime_check", {
                "check": "observe_only_" + node_type + "_result", "performed": True, "status": "failed",
                "observe_only": True, "initiator": "observer", "business_commit_claim": False,
                "semantic_quality_verified": False, "reason": proof.get("reason")})
        return proof
    except Exception as exc:  # noqa: BLE001 - an observer check must never change the native outcome
        ctx.observer.mark_gap(ctx.scope.execution_id, exc)
        return None


async def record_project_event(session, project, kind, payload):
    """C06: a project-level journal fact (no node), staged in the caller's transaction.

    Starts auto-observation if needed. Returns True when staged; False when the project has no active scope
    (observation switched off or failed), so the caller can fall back to its own log line.
    """
    observer = get_observer()
    scope = await observer.ensure_auto_scope(project)
    if scope is None:
        return False
    sync_session = getattr(session, "sync_session", session)
    source = "project" if Path(str(sync_session.bind.url.database)).name == "project.db" else "master"
    try:
        return bool(observer.stage(sync_session, scope, kind, db_scope=source, node_key=None, payload=payload))
    except Exception as exc:  # noqa: BLE001
        observer.mark_gap(scope.execution_id, exc)
        return False


@event.listens_for(Session, "before_flush")
def _stage_node_changes(session, _flush_context, _instances):
    # No scopes/default-off: not even a journal query or business-row write.
    observer = _observer
    if observer is None or not observer.scopes:
        return
    source = "project" if Path(str(session.bind.url.database)).name == "project.db" else "master"
    for row in list(session.dirty):
        if isinstance(row, Project) and inspect(row).attrs.meta.history.has_changes():
            meta = row.meta or {}
            history = inspect(row).attrs.meta.history
            before = history.deleted[0] if history.deleted else {}
            flags = ("user_stop", "mass_lane_user_stop", "auto_await_manual_start")
            if any(bool(meta.get(k)) != bool((before or {}).get(k)) for k in flags):
                stage_project_fact(session, row, "project_control", {
                    **{k: bool(meta.get(k)) for k in flags},
                    "initiator": "project_state_write", "human_identity": None,
                })
        if not isinstance(row, NodeRun):
            continue  # In particular, never observe writes to our own journal/cursors.
        scope = observer.scopes.get(row.workflow_run_id)
        if scope is None or row.node_key not in scope.node_keys:
            continue
        run = session.get(WorkflowRun, row.workflow_run_id)
        if run is None or run.project_id != scope.project_id:
            continue
        changed = {}
        for field in ("status", "progress", "progress_text", "error", "attempts"):
            history = inspect(row).attrs[field].history
            if history.has_changes():
                changed[field] = {"before": [redact(v.value if hasattr(v, "value") else v) for v in history.deleted],
                                  "after": [redact(v.value if hasattr(v, "value") else v) for v in history.added]}
        if not changed:
            continue
        ctx = _attempt.get()
        attempt_id = ctx.attempt_id if ctx and ctx.node_key == row.node_key and ctx.scope.execution_id == scope.execution_id else None
        try:
            transitions = row.__dict__.pop("_observation_transitions", [])
            observer.stage(session, scope, "node_change", db_scope=source, node_key=row.node_key,
                attempt_id=attempt_id, payload={"changes": changed, "transactional": True,
                                              "transitions": transitions,
                                              "attempt_known": attempt_id is not None})
        except Exception as exc:
            observer.mark_gap(scope.execution_id, exc)


@event.listens_for(Session, "after_commit")
def _clear_committed_observation(session):
    session.info.pop("observation_pending", None)
    if _observer:
        _observer.last_project_facts.update(session.info.pop("observation_fact_signatures", {}))


@event.listens_for(Session, "after_soft_rollback")
def _shared_commit_failure(session, previous_transaction):
    pending = session.info.get("observation_pending", set())
    if _observer is None or not pending or session.is_active:
        return
    error = getattr(session.get_transaction(), "_rollback_exception", None)
    statement = str(getattr(error, "statement", ""))
    for execution_id in pending:
        _observer.note_commit_uncertainty(execution_id, "business_commit_unconfirmed")
        if "observation_outbox" in statement:
            _observer.mark_gap(execution_id, error or RuntimeError())


def observe_check(name, summarize):
    """Wrap existing checks only; preserve return value and exception behavior."""
    def decorate(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            if _attempt.get() is None:
                return function(*args, **kwargs)
            try:
                result = function(*args, **kwargs)
            except Exception as exc:
                emit("runtime_check", {"check": name, "status": "failed", "performed": True,
                    "error_type": type(exc).__name__, "initiator": "worker"})
                raise
            emit("runtime_check", {"check": name, "performed": True, "initiator": "worker",
                                   **summarize(args, kwargs, result)})
            return result
        return wrapped
    return decorate


def stage_project_fact(session, project, kind, payload):
    """Known worker decision or project control, sharing its original transaction.

    Suppress unchanged polling facts only after a confirmed commit; a rollback
    does not suppress the next attempt to record the same fact.
    """
    observer = _observer
    if observer is None or not observer.scopes:
        return
    scope = next((s for s in observer.scopes.values() if s.project_id == project.id), None)
    if scope is None:
        return
    from app.services.excel_gpt_node import active_excel_gpt_node_key
    node_key = active_excel_gpt_node_key(project)
    if node_key not in scope.node_keys:
        if kind != "project_control":
            return
        node_key = None
    sync_session = getattr(session, "sync_session", session)
    key = (scope.execution_id, node_key, kind)
    signature = digest(payload)
    pending = sync_session.info.setdefault("observation_fact_signatures", {})
    if pending.get(key, observer.last_project_facts.get(key)) == signature:
        return
    source = "project" if Path(str(sync_session.bind.url.database)).name == "project.db" else "master"
    try:
        if observer.stage(sync_session, scope, kind, db_scope=source, node_key=node_key, payload=payload):
            pending[key] = signature
    except Exception as exc:
        observer.mark_gap(scope.execution_id, exc)


def panel_safe(value):
    """Metadata projection; full input/output bodies remain local only."""
    value = redact(value)
    if isinstance(value, dict):
        hidden = {"text", "raw", "response", "event", "accumulated_text", "ops",
                  "characters", "scenes", "prompt", "instructions", "input", "messages"}
        return {k: panel_safe(v) for k, v in value.items() if k not in hidden}
    if isinstance(value, list):
        return [panel_safe(v) for v in value]
    if isinstance(value, str):
        return re.sub(r"[A-Za-z]:[\\/][^\s\"']*|\\\\[^\s\"']+|/(?:srv|results|home|root|tmp|observe-source)/[^\s\"']*",
                      "[local path]", value)
    return value


def observation_tables():
    """Orchestrator-owned tables created by the observation migration (also its schema fingerprint)."""
    from app.services.diagnosis import Incident, DiagnosisState
    from app.services.actions_v1 import ActionExecutionControl, ActionRecord, ManagedRunAllowance
    from app.services.actions_recovery import RecoveryAction
    from app.services.decision_state import DecisionControl, DecisionRecord, DecisionTurn
    from app.services.profiles_state import ProfileOverlay
    from app.services.decision_config import DecisionRuntimeBinding
    from app.services.media_jobs import MediaJob, MediaRequest
    from app.services.managed_launch import ManagedLaunch
    from app.services.kill_switch import KillSwitchHistory, KillSwitchState
    return [
        ExecutionControl.__table__, ObservationOutbox.__table__, ObservationEvent.__table__,
        Incident.__table__, DiagnosisState.__table__, ActionExecutionControl.__table__,
        ActionRecord.__table__, RecoveryAction.__table__,
        DecisionControl.__table__, DecisionRecord.__table__, DecisionTurn.__table__, ProfileOverlay.__table__, DecisionRuntimeBinding.__table__,
        MediaJob.__table__, MediaRequest.__table__,
        ManagedLaunch.__table__, ManagedRunAllowance.__table__,
        ObservationDegradation.__table__, CommitUncertaintyMarker.__table__,
        KillSwitchState.__table__, KillSwitchHistory.__table__,
    ]


async def migrate_observation_schema(conn):
    """Additive migration used by the same startup path as existing schema migrations."""
    tables = observation_tables()
    await conn.run_sync(lambda connection: Base.metadata.create_all(connection, tables=tables))


async def business_snapshot(session, scope):
    project = await session.get(Project, scope.project_id)
    run = await session.get(WorkflowRun, scope.workflow_run_id)
    nodes = (await session.scalars(select(NodeRun).where(
        NodeRun.workflow_run_id == scope.workflow_run_id, NodeRun.node_key.in_(scope.node_keys),
    ))).all()
    return {"project_status": project.status.value if project else None,
        "workflow_status": run.status.value if run else None,
        "nodes": {n.node_key: {"status": n.status.value, "progress": n.progress,
            "progress_text": redact(n.progress_text), "error": redact(n.error),
            "attempts": n.attempts} for n in nodes}}, project


async def native_approval_evidence(session, scope):
    from sqlalchemy import func
    latest = select(func.max(ObservationEvent.id)).where(
        ObservationEvent.execution_id == scope.execution_id,
        ObservationEvent.project_id == scope.project_id,
        ObservationEvent.node_key.in_(scope.node_keys),
        ObservationEvent.kind.in_(['node_start', 'human_approval_pending']),
    ).group_by(ObservationEvent.node_key, ObservationEvent.kind)
    return (await session.scalars(select(ObservationEvent).where(ObservationEvent.id.in_(latest)))).all()


async def pending_approval_context(session, scope, events):
    """Read only committed, scoped HITL creation plus the current native decision.

    Native text nodes can be done while approval is pending. Content/approval
    state is authoritative in project.db; it is not a master replica mismatch.
    """
    from app.models import HITLRequest, HITLDecision
    latest_starts = {}
    approvals = {}
    for event in sorted(events, key=lambda row: row.id):
        if (event.execution_id != scope.execution_id or event.project_id != scope.project_id
                or event.node_key not in scope.node_keys):
            continue
        if event.kind == 'node_start':
            latest_starts[event.node_key] = event.attempt_id
            approvals.pop(event.node_key, None)
        elif (event.kind == 'human_approval_pending' and event.attempt_id
                and latest_starts.get(event.node_key) == event.attempt_id):
            payload = event.payload or {}
            if (payload.get('kind') in {'approve_plan', 'approve_script'}
                    and type(payload.get('hitl_request_id')) is int):
                approvals[event.node_key] = event
    if not approvals:
        return {}
    ids = [event.payload['hitl_request_id'] for event in approvals.values()]
    pending = {row.id: row for row in (await session.scalars(select(HITLRequest).where(
        HITLRequest.project_id == scope.project_id, HITLRequest.id.in_(ids),
        HITLRequest.decision == HITLDecision.pending))).all()}
    result = {}
    for key, event in approvals.items():
        row = pending.get(event.payload['hitl_request_id'])
        if row and row.kind.value == event.payload['kind']:
            result[key] = {**event.payload, 'event_id': event.event_id,
                'attempt_id': event.attempt_id, 'decision': 'pending'}
    return result


def confirmed_qc_noop(events, request_id):
    """Request/attempt-scoped evidence, not a semantic quality verdict.

    Caller supplies one attempt. DB completion and current-attempt checks are
    separate, so a parsed no-op cannot close another attempt or DB incident.
    """
    if not request_id:
        return False
    related = [e for e in events if e.get('request_id') == request_id]
    parsed = any(e['kind'] == 'parse_result' and accepted_qc_noop_parse(e['payload']) for e in related)
    complete = (any(e['kind'] == 'model_start' for e in related)
        and any(e['kind'] == 'model_end' and e['payload'].get('complete') is True for e in related))
    checked = any(e['kind'] == 'runtime_check' and e['payload'].get('check') == 'qc_noop'
        and e['payload'].get('performed') is True and e['payload'].get('status') == 'passed'
        and e['payload'].get('empty_ops_allowed') is True
        and e['payload'].get('response_complete') is True
        and set(e['payload'].get('required_checks', [])) >= {
            'strict_json_ops_array','completed_response','trusted_empty_ops_policy'} for e in related)
    failed = any(e['kind'] in {'node_error','apply_error'} or
        (e['kind'] == 'model_error' and e.get('request_id') == request_id) or
        (e['kind'] == 'runtime_check' and e['payload'].get('performed') is True
            and e['payload'].get('status') in {'failed','rejected'}) or
        (e['kind'] == 'domain_check' and e['payload'].get('verdict') in {'fail','reject'})
        for e in events)
    return parsed and complete and checked and not failed


def accepted_qc_noop_parse(payload):
    """Format acceptance only. Checks, attempt completion and quality are separate."""
    return (all(payload.get(k) is True for k in ('syntax_valid','parsed','schema_valid',
        'ops_field_present','ops_type_valid','empty_ops_allowed','empty_ops_accepted'))
        and payload.get('ops_count') == 0 and payload.get('payload_present') is False)
