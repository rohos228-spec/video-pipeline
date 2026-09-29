"""Explicit, bounded execution permissions layered over Observe-v1.

This module deliberately contains no planner or model-driven action selection.
Managed execution is opt-in, scope-bound, and defaults to disabled.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from threading import RLock
import uuid

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, UniqueConstraint, select, update
from sqlalchemy.orm import Mapped, mapped_column
from app.models import Base
from app.services.decision_state import DecisionControl, DecisionRecord, DecisionTurn


class ActionExecutionControl(Base):
    """Durable opt-in permission; absent rows mean disabled."""
    __tablename__ = "action_execution_controls"
    execution_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    workflow_run_id: Mapped[int] = mapped_column(Integer, index=True)
    group_id: Mapped[str] = mapped_column(String(120))
    node_keys: Mapped[list] = mapped_column(JSON)
    graph_version: Mapped[str] = mapped_column(String(64))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    held: Mapped[bool] = mapped_column(Boolean, default=False)
    request_limit: Mapped[int] = mapped_column(Integer, default=12)
    requests_used: Mapped[int] = mapped_column(Integer, default=0)
    initiator: Mapped[str] = mapped_column(String(120))
    rationale: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ActionRecord(Base):
    """Append-only audit facts for explicit operator actions and verification."""
    __tablename__ = "action_v1_records"
    __table_args__ = (UniqueConstraint("execution_id", "idempotency_key", name="uq_action_execution_idempotency"),)
    action_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[int] = mapped_column(Integer, index=True)
    action_kind: Mapped[str] = mapped_column(String(40))
    node_key: Mapped[str | None] = mapped_column(String(60), default=None)
    attempt_id: Mapped[str | None] = mapped_column(String(36), default=None)
    initiator: Mapped[str] = mapped_column(String(120))
    rationale: Mapped[str] = mapped_column(Text)
    evidence_ids: Mapped[list] = mapped_column(JSON, default=list)
    preconditions: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(24), default="accepted")
    verification: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


@dataclass(frozen=True)
class ExecutionPermission:
    enabled: bool
    held: bool
    project_id: int
    execution_id: str
    group_id: str
    graph_version: str
    node_keys: tuple[str, ...]
    request_limit: int
    requests_used: int


@dataclass(frozen=True)
class StartDecision:
    allowed: bool
    reason: str


_permissions: dict[int, ExecutionPermission] = {}
_permission_lock = RLock()


def install_permission(permission: ExecutionPermission) -> None:
    """Install an explicitly-authorized managed profile for this process.

    Callers must persist the corresponding profile before installation. Startup
    defaults to an empty registry, so a restart cannot accidentally authorize
    execution if profile reload/validation fails.
    """
    with _permission_lock:
        _permissions[permission.project_id] = permission


def clear_permission(project_id: int) -> None:
    with _permission_lock:
        _permissions.pop(project_id, None)
    from app.services.actions_recovery import clear_restriction
    clear_restriction(project_id)


def get_permission(project_id: int) -> ExecutionPermission | None:
    with _permission_lock:
        return _permissions.get(project_id)


async def managed_project_enabled(project_id: int) -> bool:
    """Legacy tools fail closed even if volatile permissions were not reloaded."""
    if get_permission(project_id) is not None:
        return True
    from app.services.observation import get_observer
    async with get_observer().master() as session:
        return await session.scalar(select(ActionExecutionControl.execution_id).where(
            ActionExecutionControl.project_id == project_id,
            ActionExecutionControl.enabled.is_(True)).limit(1)) is not None


def update_permission(project_id: int, *, held: bool | None = None,
                      requests_used: int | None = None) -> ExecutionPermission | None:
    """Atomically update volatile gate state after a durable control write."""
    from dataclasses import replace
    with _permission_lock:
        current = _permissions.get(project_id)
        if current is None:
            return None
        changes = {}
        if held is not None:
            changes["held"] = held
        if requests_used is not None:
            changes["requests_used"] = max(current.requests_used, requests_used)
        current = replace(current, **changes)
        _permissions[project_id] = current
        return current


def check_managed_start(*, project_id: int | None, node_key: str) -> StartDecision | None:
    """Return None for unmanaged work; otherwise enforce the managed scope."""
    if project_id is None:
        return None
    permission = get_permission(project_id)
    from app.services.actions_recovery import restricted_node
    recovery_node = restricted_node(project_id)
    if recovery_node is not None and node_key != recovery_node:
        return StartDecision(False, "recovery_verification_pending")
    if permission is None:
        # A persisted managed execution without a loaded/valid profile is
        # blocked, never silently treated as ordinary pipeline work.
        try:
            from app.services.observation import get_observer
            known_managed = any(
                scope.project_id == project_id and scope.mode == "managed"
                for scope in get_observer().scopes.values()
            )
        except Exception:
            known_managed = True
        if known_managed:
            return StartDecision(False, "managed_permission_unavailable")
        return None
    return evaluate_start(permission, project_id=project_id, node_key=node_key,
                          graph_version=permission.graph_version)


async def reload_permissions(master) -> None:
    """Fail closed unless durable profiles can be loaded and validated."""
    with _permission_lock:
        _permissions.clear()
    async with master() as session:
        controls = (await session.scalars(select(ActionExecutionControl).where(
            ActionExecutionControl.enabled.is_(True),
        ))).all()
    with _permission_lock:
        for item in controls:
            _permissions[item.project_id] = ExecutionPermission(
                enabled=item.enabled, held=item.held, project_id=item.project_id,
                execution_id=item.execution_id, group_id=item.group_id,
                graph_version=item.graph_version, node_keys=tuple(item.node_keys or ()),
                request_limit=item.request_limit, requests_used=item.requests_used,
            )
    from app.services.actions_recovery import reload_recovery_restrictions
    await reload_recovery_restrictions(master)


class ActionSupervisor:
    """Durable operator controls for an already-observed execution."""
    def __init__(self, master):
        self.master = master

    async def recover_node(self, **kwargs):
        from app.services.actions_recovery import recover_node
        return await recover_node(self, **kwargs)

    async def enable(self, *, project_id: int, execution_id: str, group_id: str,
                     node_keys: list[str], graph_version: str, initiator: str,
                     rationale: str, request_limit: int = 12) -> dict:
        from sqlalchemy import select
        from app.models import Project, WorkflowRun, NodeRun, NodeRunStatus
        from app.services.observation import ExecutionControl, digest
        if not initiator or not rationale.strip() or not group_id.strip():
            raise ValueError("initiator, group id and rationale are required")
        if not 1 <= request_limit <= 100:
            raise ValueError("request limit must be between 1 and 100")
        if not node_keys or len(node_keys) != len(set(node_keys)):
            raise ValueError("explicit unique node scope required")
        async with self.master() as session:
            scope = await session.get(ExecutionControl, execution_id)
            if scope is None or scope.project_id != project_id:
                raise LookupError("execution not found for project")
            run = await session.get(WorkflowRun, scope.workflow_run_id)
            if run is None or run.project_id != project_id:
                raise ValueError("workflow run/project mismatch")
            live_version = digest({"nodes": run.nodes_snapshot, "edges": run.edges_snapshot})
            if live_version != graph_version or live_version != scope.graph_version:
                raise ValueError("workflow graph changed; re-observe before enabling actions")
            if scope.observer_state != "active" or scope.mode != "observe":
                raise ValueError("active observation required")
            if not set(node_keys).issubset(scope.node_keys):
                raise ValueError("action node scope exceeds observation scope")
            graph_group_keys = {
                str(node.get("id")) for node in (run.nodes_snapshot or [])
                if isinstance(node, dict)
                and str((node.get("data") or {}).get("groupId") or "") == group_id
            }
            if not graph_group_keys or graph_group_keys != set(node_keys):
                raise ValueError("action scope must equal the complete selected graph group")
            project = await session.get(Project, project_id)
            if project is None or bool((project.meta or {}).get("user_stop")):
                raise ValueError("missing project or user_stop is active")
            rows = (await session.scalars(select(NodeRun).where(
                NodeRun.workflow_run_id == scope.workflow_run_id,
                NodeRun.node_key.in_(node_keys),
            ))).all()
            if any(row.status in {NodeRunStatus.running, NodeRunStatus.queued} for row in rows):
                raise ValueError("selected group already has queued/running nodes")
            if {row.node_key for row in rows} != set(node_keys):
                raise ValueError("selected nodes do not match workflow run")
            other_controls = (await session.scalars(select(ActionExecutionControl).where(
                ActionExecutionControl.project_id == project_id,
                ActionExecutionControl.enabled.is_(True),
                ActionExecutionControl.execution_id != execution_id,
            ))).all()
            if other_controls:
                raise ValueError("another managed execution is still enabled for this project")
            prior = await session.get(ActionExecutionControl, execution_id)
            if prior is not None:
                if (prior.enabled and prior.project_id == project_id
                        and prior.node_keys == list(node_keys)
                        and prior.graph_version == live_version
                        and prior.group_id == group_id
                        and prior.request_limit == request_limit):
                    result = {"enabled": True, "held": prior.held,
                        "request_limit": prior.request_limit, "requests_used": prior.requests_used,
                        "node_keys": list(prior.node_keys), "group_id": prior.group_id}
                    install_permission(ExecutionPermission(
                        enabled=True, held=prior.held, project_id=project_id,
                        execution_id=execution_id, group_id=group_id,
                        graph_version=live_version, node_keys=tuple(node_keys),
                        request_limit=prior.request_limit, requests_used=prior.requests_used,
                    ))
                    return result
                raise ValueError("managed permission already exists with different scope")
            control = ActionExecutionControl(
                execution_id=execution_id, project_id=project_id,
                workflow_run_id=scope.workflow_run_id, group_id=group_id,
                node_keys=list(node_keys), graph_version=live_version,
                enabled=True, held=False, request_limit=request_limit,
                requests_used=0, initiator=initiator, rationale=rationale.strip(),
            )
            session.add(control)
            scope.mode = "managed"
            session.add(ActionRecord(
                action_id=str(uuid.uuid4()), execution_id=execution_id,
                project_id=project_id, action_kind="enable", initiator=initiator,
                rationale=rationale.strip(), evidence_ids=[],
                preconditions={"graph_version_matches": True, "scope_subset": True,
                    "user_stop": False, "selected_nodes_idle": True},
                idempotency_key="enable:" + execution_id, status="accepted", verification={},
            ))
            await session.commit()
        install_permission(ExecutionPermission(
            enabled=True, held=False, project_id=project_id, execution_id=execution_id,
            group_id=group_id, graph_version=live_version, node_keys=tuple(node_keys),
            request_limit=request_limit, requests_used=0,
        ))
        from app.services.observation import get_observer
        cached_scope = get_observer().scopes.get(scope.workflow_run_id)
        if cached_scope is not None and cached_scope.execution_id == execution_id:
            cached_scope.mode = "managed"
        return {"enabled": True, "held": False, "request_limit": request_limit,
                "requests_used": 0, "node_keys": list(node_keys), "group_id": group_id}

    async def set_held(self, *, project_id: int, execution_id: str, held: bool,
                       initiator: str, rationale: str, idempotency_key: str) -> dict:
        from sqlalchemy import select
        from app.models import Project
        if not initiator or not rationale.strip() or not idempotency_key.strip():
            raise ValueError("explicit initiator, rationale and idempotency key required")
        async with self.master() as session:
            control = await session.get(ActionExecutionControl, execution_id)
            if control is None or control.project_id != project_id or not control.enabled:
                raise LookupError("managed execution not found")
            existing = (await session.scalars(select(ActionRecord).where(
                ActionRecord.execution_id == execution_id,
                ActionRecord.idempotency_key == idempotency_key,
            ))).first()
            if existing is not None:
                return {"enabled": control.enabled, "held": control.held,
                        "node_keys": list(control.node_keys), "action_id": existing.action_id,
                        "idempotent_replay": True}
            if not held:
                project = await session.get(Project, project_id)
                if project is None or bool((project.meta or {}).get("user_stop")):
                    raise ValueError("user_stop is active; explicit user-stop gate is never cleared here")
            control.held = held
            session.add(ActionRecord(
                action_id=str(uuid.uuid4()), execution_id=execution_id,
                project_id=project_id, action_kind="resume" if not held else "hold",
                initiator=initiator, rationale=rationale.strip(), evidence_ids=[],
                preconditions={"explicit_operator_request": True},
                idempotency_key=idempotency_key,
                status="accepted", verification={},
            ))
            await session.commit()
            response = {"enabled": control.enabled, "held": control.held,
                        "node_keys": list(control.node_keys), "idempotent_replay": False}
        update_permission(project_id, held=held)
        return response

    async def charge_request(self, execution_id: str) -> int:
        """Durably charge a model attempt before the provider transport starts."""
        async with self.master() as session:
            reserved = (await session.execute(update(ActionExecutionControl).where(
                ActionExecutionControl.execution_id == execution_id,
                ActionExecutionControl.enabled.is_(True), ActionExecutionControl.held.is_(False),
                ActionExecutionControl.requests_used < ActionExecutionControl.request_limit,
            ).values(requests_used=ActionExecutionControl.requests_used + 1).returning(
                ActionExecutionControl.requests_used, ActionExecutionControl.project_id))).first()
            if reserved is None:
                control = await session.get(ActionExecutionControl, execution_id)
                if control is None or not control.enabled or control.held:
                    raise RuntimeError("managed execution is absent, disabled, or held")
                raise RuntimeError("managed execution request budget exhausted")
            used, project_id = reserved
            await session.commit()
        update_permission(project_id, requests_used=used)
        return used

    async def close(self, *, project_id: int, execution_id: str, initiator: str,
                    rationale: str, idempotency_key: str) -> dict:
        """Revoke only when no managed node is queued/running; never stop business work."""
        from app.models import NodeRun, NodeRunStatus
        from app.services.observation import ExecutionControl
        if not initiator or not rationale.strip() or not idempotency_key.strip():
            raise ValueError("initiator, rationale and idempotency key required")
        async with self.master() as session:
            control = await session.get(ActionExecutionControl, execution_id)
            if control is None or control.project_id != project_id:
                raise LookupError("managed execution not found")
            workflow_run_id = control.workflow_run_id
            prior = (await session.scalars(select(ActionRecord).where(
                ActionRecord.execution_id == execution_id,
                ActionRecord.idempotency_key == idempotency_key,
            ))).first()
            if prior is not None:
                return {"enabled": control.enabled, "held": control.held,
                        "action_id": prior.action_id, "idempotent_replay": True}
            active = (await session.scalars(select(NodeRun).where(
                NodeRun.workflow_run_id == control.workflow_run_id,
                NodeRun.node_key.in_(control.node_keys),
                NodeRun.status.in_([NodeRunStatus.queued, NodeRunStatus.running]),
            ))).all()
            if active:
                raise ValueError("cannot revoke while a managed node is queued/running")
            from app.services.actions_recovery import RecoveryAction
            recovering = await session.scalar(select(RecoveryAction).where(
                RecoveryAction.execution_id == execution_id,
                RecoveryAction.status.in_(["reserved", "queued", "claimed", "await_verify"])))
            if recovering:
                raise ValueError("cannot revoke while a recovery ticket is reserved or awaiting verification")
            scope = await session.get(ExecutionControl, execution_id)
            if scope is not None:
                scope.mode = "observe"
            control.enabled = False
            control.held = True
            action_id = str(uuid.uuid4())
            session.add(ActionRecord(action_id=action_id, execution_id=execution_id,
                project_id=project_id, action_kind="close_execution", initiator=initiator,
                rationale=rationale.strip(), evidence_ids=[],
                preconditions={"no_managed_node_active": True},
                idempotency_key=idempotency_key, status="revoked",
                verification={"business_action": False}))
            await session.commit()
        clear_permission(project_id)
        from app.services.observation import get_observer
        cached = get_observer().scopes.get(workflow_run_id)
        if cached is not None and cached.execution_id == execution_id:
            cached.mode = "observe"
        return {"enabled": False, "held": True, "action_id": action_id,
                "idempotent_replay": False}


def evaluate_start(permission: ExecutionPermission | None, *, project_id: int,
                   node_key: str, graph_version: str) -> StartDecision:
    """Pure fail-closed gate used immediately before a managed node starts."""
    if permission is None or not permission.enabled:
        return StartDecision(False, "execution_not_explicitly_enabled")
    if permission.held:
        return StartDecision(False, "execution_held")
    if permission.project_id != project_id or node_key not in permission.node_keys:
        return StartDecision(False, "outside_execution_scope")
    if permission.graph_version != graph_version:
        return StartDecision(False, "graph_version_changed")
    if permission.requests_used >= permission.request_limit:
        return StartDecision(False, "request_budget_exhausted")
    return StartDecision(True, "authorized")


def action_is_idempotent(existing_execution_id: str, existing_action_id: str,
                         execution_id: str, action_id: str) -> bool:
    """True only when the exact action identity has already been accepted."""
    return existing_execution_id == execution_id and existing_action_id == action_id
