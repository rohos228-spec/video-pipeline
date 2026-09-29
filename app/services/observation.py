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
import time
import uuid
from contextlib import asynccontextmanager, ExitStack
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from loguru import logger
from sqlalchemy import JSON, String, UniqueConstraint, event, inspect, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.models import Base, NodeRun, Project, WorkflowRun
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


_CREDENTIAL_FIELDS = {"authorization", "proxy_authorization", "cookie", "set_cookie",
                      "api_key", "apikey", "access_token", "refresh_token", "id_token",
                      "password", "secret", "client_secret", "token"}
_CREDENTIAL_TEXT = re.compile(
    r"(?i)(\bBearer\s+)[^\s\"'<>]+|"
    r"(\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|cookie)[\"']?\s*[:=]\s*[\"']?)[^\s\"'<>;,}\]]+|"
    r"\bsk-[A-Za-z0-9_-]{8,}"
)


def redact(value):
    """Allow numeric usage; redact credentials by exact field or known syntax."""
    if isinstance(value, dict):
        return {str(k): "[redacted]" if (str(k).lower().replace("-", "_") in _CREDENTIAL_FIELDS
                or str(k).lower().replace("-", "_").endswith(("_api_key", "_access_token", "_refresh_token", "_secret")))
                else redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _CREDENTIAL_TEXT.sub(lambda m: (m.group(1) or m.group(2) or "") + "[redacted]", value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact(str(value))


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


_attempt = contextvars.ContextVar("observation_attempt", default=None)
_request = contextvars.ContextVar("observation_request", default=None)
_observer = None


class Observer:
    MAX_BODY = 1024 * 1024
    MAX_TOTAL = 32 * 1024 * 1024
    RETENTION_SECONDS = 7 * 24 * 3600

    def __init__(self, master_factory, *, body_root: Path):
        self.master = master_factory
        self.body_root = Path(body_root)
        self.scopes: dict[int, ExecutionControl] = {}
        self._delivery_lock = asyncio.Lock()
        self._enable_lock = asyncio.Lock()
        self.failures: dict[str, str] = {}
        self.commit_uncertainty: dict[str, str] = {}
        self.last_project_facts: dict[tuple, str] = {}

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
            return scope

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
                await migrate_observation_schema(conn)

    async def reload(self):
        async with self.master() as session:
            scopes = (await session.scalars(select(ExecutionControl).where(
                ExecutionControl.observer_state.in_(["active", "degraded"])
            ))).all()
        for scope in scopes:
            await self.ensure_project_schema(scope.project_id)
        self.scopes = {s.workflow_run_id: s for s in scopes}
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
        total = 0
        # No automatic deletion: diagnostic evidence, pinned and active scopes
        # survive age and restart. Exhaustion is an explicit body_total_limit gap.
        for item in self.body_root.glob("*/*.json"):
            stat = item.stat()
            total += stat.st_size
        sha = hashlib.sha256(raw).hexdigest()
        path = folder / (sha + ".json")
        if not path.exists():
            if total + len(raw) > self.MAX_TOTAL:
                return {"gap": "body_total_limit", "bytes": len(raw)}
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        return {"path": path.relative_to(self.body_root).as_posix(), "sha256": sha, "bytes": len(raw)}

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

    def mark_gap(self, execution_id, error):
        self.failures[execution_id] = type(error).__name__
        cached = next((s for s in self.scopes.values() if s.execution_id == execution_id), None)
        if cached:
            cached.observer_state = "degraded"
        logger.warning("observer degraded: execution={} error_type={}", execution_id, type(error).__name__)

    async def catch_up(self):
        from app.project_db import get_project_sessionmaker
        # Stopped scopes may still have a transaction committing its recorded
        # tail. Delivery remains enabled; production hooks stay disabled.
        async with self.master() as session:
            deliveries = (await session.scalars(select(ExecutionControl))).all()
        for scope in deliveries:
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
                if scope.execution_id in self.failures:
                    async with self.master() as session:
                        stored = await session.get(ExecutionControl, scope.execution_id)
                        if stored.observer_state != "stopped":
                            stored.observer_state = "degraded"
                        stored.gaps = list(stored.gaps or []) + [{"error_type": self.failures[scope.execution_id],
                            "at": datetime.utcnow().isoformat(), "detail": "Observer data may be incomplete"}]
                        await session.commit()
                    self.failures.pop(scope.execution_id, None)
            except Exception as exc:
                self.mark_gap(scope.execution_id, exc)

    async def loop(self):
        loaded = False
        while True:
            try:
                if not loaded:
                    await self.reload()
                    loaded = True
                await self.catch_up()
                from app.services.diagnosis import get_diagnoser
                await get_diagnoser(self).catch_up()
                from app.services.actions_recovery import reconcile
                await reconcile(self)
                from app.services.decision_v1 import get_decider
                await get_decider(self).tick()
            except Exception as exc:
                for scope in self.scopes.values():
                    self.mark_gap(scope.execution_id, exc)
                logger.warning("observer consumer unavailable: {}", type(exc).__name__)
            await asyncio.sleep(1)


def get_observer():
    global _observer
    if _observer is None:
        from app.db import SessionLocal
        from app.settings import settings
        _observer = Observer(SessionLocal, body_root=Path(settings.data_dir) / "observations" / "bodies")
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
    except Exception as exc:
        ctx.observer.mark_gap(ctx.scope.execution_id, exc)


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
            return await function(*args, **kwargs)
        request_id = str(uuid.uuid4())
        previous_request_id = ctx.last_request_id
        from app.services.profiles_v1 import transform_request
        kwargs = await transform_request(ctx, kwargs)
        body = kwargs.get("body") or {}
        if ctx.scope.mode == "managed":
            from app.services.actions_recovery import check_body
            await check_body(ctx, digest(body))
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
                raise
        await record_fact("model_start", {"method": "POST", "host": endpoint.hostname,
            "path": endpoint.path, "model": kwargs.get("use_model"),
            "previous_request_id": previous_request_id,
            "settings_revision": digest(settings), "input_revision": digest(body)}, body=body)
        try:
            result = await function(*args, **kwargs)
            raw = getattr(result, "raw", {}) or {}
            await record_fact("model_end", {"model": getattr(result, "model", None),
                "response_id": getattr(result, "response_id", None),
                "finish_reason": getattr(result, "finish_reason", None),
                "usage": getattr(result, "usage", None),
                "complete": raw.get("status") == "completed" and not raw.get("sse_salvaged"),
                "retrieved_by_id": bool(raw.get("retrieved_by_id")),
            }, body={"text": getattr(result, "text", ""), "response": raw})
            return result
        except BaseException as exc:
            from app.services.error_catalog import describe_error
            context = getattr(exc,"context",None) or {}
            await record_fact("model_error", {"error_type": type(exc).__name__, "complete": False,
                "error_code":describe_error(exc)[0],"error_kind":context.get("error_kind") if isinstance(context,dict) else None,
                "catalog_source":"structured_exception_context" if isinstance(context,dict) and context.get("error_kind") else "exception_type_or_text",
                "status_code":context.get("status_code") if isinstance(context,dict) else None,
                "uncertain":type(exc).__name__ in {"CancelledError","ReadTimeout"} or (isinstance(context,dict) and context.get("error_kind") in {"timeout","network"})})
            raise
        finally:
            _request.reset(request_token)
    return wrapped


async def record_parse(text, parsed, *, payload_required=True):
    try:
        json.loads(text)
        syntax_valid = True
    except (ValueError, TypeError):
        syntax_valid = False
    ctx = _attempt.get()
    await record_fact("parse_result", {"syntax_valid": syntax_valid,
        "parsed": parsed is not None, "schema_valid": None,
        "contract": "json_apply_ops" if payload_required else "text_verdict_or_optional_apply_ops",
        "schema_note": "Extraction does not prove node schema validation",
        "payload_required": payload_required,
        "payload_present": bool(parsed and any(parsed.get(k) for k in ("ops", "characters", "scenes", "actions"))),
        "ops_count": len((parsed or {}).get("ops") or [])}, body={"text": text, "parsed": parsed},
        request_id=ctx.last_request_id if ctx else None)


def observe_apply(function):
    @functools.wraps(function)
    async def wrapped(session, project, *args, **kwargs):
        if _attempt.get() is None:
            return await function(session, project, *args, **kwargs)
        emit("apply_input", {"node_kind": kwargs.get("node_kind")}, body={"ops": args[0] if args else kwargs.get("ops"),
            "characters": kwargs.get("characters"), "scenes": kwargs.get("scenes")})
        try:
            result = await function(session, project, *args, **kwargs)
        except BaseException as exc:
            await record_fact("apply_error", {"error_type": type(exc).__name__})
            raise
        emit("data_applied", {"result": result, "transactional": True},request_id=_attempt.get().last_request_id)
        emit("runtime_check", {"check": "apply_ops_validation", "status": "passed",
            "performed": True, "result": result, "initiator": "worker",
            "business_commit_claim": False})
        return result
    return wrapped


@asynccontextmanager
async def observe_attempt(session, project):
    observer = get_observer()
    from app.services.excel_gpt_node import active_excel_gpt_node_key
    node_key = active_excel_gpt_node_key(project)
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
    if scope.mode == "managed":
        from app.services.actions_recovery import claim, input_revision
        claimed_id = await claim(observer, session, project, node_key, attempt_id, execution_id=scope.execution_id)
        if claimed_id != attempt_id:
            session.info["recovery_attempt_id"] = claimed_id
        attempt_id = claimed_id
        revision = await input_revision(session, project, node_key, parts=revision_parts)
    ctx = AttemptContext(observer, scope, session, source, node_key, attempt_id)
    token = _attempt.set(ctx)
    await record_fact("node_start", {"project_status": project.status.value, "node_contract": node_key,
                                  "recovery_input_revision": revision, "recovery_revision_parts": revision_parts})
    profile_stack = ExitStack()
    try:
        from app.services.profiles_v1 import load_attempt, bind_attempt
        overlay = await load_attempt(ctx)
        profile_stack.enter_context(bind_attempt(overlay))
        if overlay:
            await record_fact("profile_effective_context", {k:overlay[k] for k in ("overlay_id","profile_id","candidate_hash","base_version","attempt_id","candidate")})
        yield
    except BaseException as exc:
        from app.services.profiles_v1 import finish_attempt
        await finish_attempt(ctx, True)
        await record_fact("node_error", {"error_type": type(exc).__name__})
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
        profile_stack.close()
        _attempt.reset(token)
        session.info.pop("recovery_attempt_id", None)


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
        _observer.commit_uncertainty[execution_id] = "business_commit_unconfirmed"
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


async def migrate_observation_schema(conn):
    """Additive migration used by the same startup path as existing schema migrations."""
    from app.services.diagnosis import Incident, DiagnosisState
    from app.services.actions_v1 import ActionExecutionControl, ActionRecord
    from app.services.actions_recovery import RecoveryAction
    from app.services.decision_state import DecisionControl, DecisionRecord, DecisionTurn
    from app.services.profiles_state import ProfileOverlay
    await conn.run_sync(lambda connection: Base.metadata.create_all(connection, tables=[
        ExecutionControl.__table__, ObservationOutbox.__table__, ObservationEvent.__table__,
        Incident.__table__, DiagnosisState.__table__, ActionExecutionControl.__table__,
        ActionRecord.__table__, RecoveryAction.__table__,
        DecisionControl.__table__, DecisionRecord.__table__, DecisionTurn.__table__, ProfileOverlay.__table__,
    ]))


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
