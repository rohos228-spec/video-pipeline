"""One-shot recovery tickets for the existing worker, never manual run_step.

Reservation precedes dispatch; claim precedes HTTP. Ambiguous dispatch/claim is
held for a human, never replayed. Continuation uses the original graph planner.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import JSON, String, UniqueConstraint, select, update
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, NodeRun, NodeRunStatus, Project, ProjectStatus, WorkflowRun
from app.services.actions_v1 import ActionExecutionControl, ActionRecord, update_permission
from app.services import revisions

_active_nodes: dict[int, str] = {}
_inflight_projects: set[int] = set()


def restricted_node(project_id):
    return _active_nodes.get(project_id)


def clear_restriction(project_id):
    _active_nodes.pop(project_id, None)


def finish_job_tracking(project_id):
    _inflight_projects.discard(project_id)


async def reload_recovery_restrictions(master):
    _active_nodes.clear()
    async with master() as s:
        rows = (await s.scalars(select(RecoveryAction).join(ActionExecutionControl,
            RecoveryAction.execution_id == ActionExecutionControl.execution_id).where(
            ActionExecutionControl.enabled.is_(True), RecoveryAction.status.in_(
            ["reserved", "queued", "claimed", "await_verify", "human_review"])))).all()
        for row in rows:
            _active_nodes[row.project_id] = row.node_key


class RecoveryAction(Base):
    __tablename__ = "action_node_recoveries"
    __table_args__ = (
        UniqueConstraint("execution_id", "node_key", name="uq_single_node_recovery"),
        UniqueConstraint("execution_id", "idempotency_key", name="uq_recovery_key"),
    )
    action_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[int] = mapped_column(index=True)
    incident_id: Mapped[str] = mapped_column(String(36))
    node_key: Mapped[str] = mapped_column(String(60))
    source_attempt_id: Mapped[str] = mapped_column(String(36))
    new_attempt_id: Mapped[str] = mapped_column(String(36))
    idempotency_key: Mapped[str] = mapped_column(String(120))
    preconditions: Mapped[dict] = mapped_column(JSON)
    budget_state: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    verification_result: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(24), default="reserved")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


def ticket_json(row):
    return {key: getattr(row, key) for key in (
        "action_id", "execution_id", "incident_id", "node_key", "source_attempt_id",
        "new_attempt_id", "idempotency_key", "preconditions", "budget_state", "result",
        "verification_result", "status", "created_at")}


# Native text nodes name their attached prompt files with a wall-clock stamp
# (prompt_plan_20261001_205923.txt) that appears inside the request text. Only a node
# whose capability declares volatile_attachment_names compares first request bodies with
# these stamps masked; every other byte of the body (including all attached contents,
# which are inlined) must still be identical.
_ATTACHMENT_STAMP = re.compile(r"(?<=_)\d{8}_\d{6}(?=\.[A-Za-z0-9]{1,5})")
STAMP_NORMALIZATION = "attachment_timestamps"


def body_identity(body, *, normalize_names=False):
    from app.services.observation import digest
    if not normalize_names:
        return digest(body)
    text = json.dumps(body, ensure_ascii=False, sort_keys=True, default=str)
    return digest(_ATTACHMENT_STAMP.sub("<ts>", text))


def load_source_body(observer, event):
    """The recorded source request body, only if its stored bytes and hashes still agree."""
    from app.services.observation import digest
    ref = event.payload.get("body") or {}
    root = observer.body_root.resolve()
    path = (observer.body_root / ref.get("path", "")).resolve()
    if not ref.get("sha256") or not path.is_relative_to(root) or not path.is_file():
        raise ValueError("source request body unavailable for recovery body comparison")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise ValueError("source request body hash mismatch")
    body = json.loads(raw)
    if digest(body) != event.payload.get("input_revision"):
        raise ValueError("source request body does not match its recorded revision")
    return body


async def input_revision(session, project, node_key, *, parts=None):
    """Only hashes leave the process. Runtime bookkeeping is not node input.

    Scoped to the node: its data inputs, resolved prompt files, capability data,
    model-request settings, own configuration and a bounded list of code
    modules (see app.services.revisions). Unrelated prompts, settings and code
    do not enter it. In addition, the first recovery HTTP body must equal the
    source body hash, which catches file-derived inputs not represented here.
    """
    from app.services.revisions import compute_input_revision
    return await compute_input_revision(session, project, node_key, parts=parts)


async def recover_node(supervisor, *, project_id, execution_id, incident_id, node_key,
                       idempotency_key, initiator, rationale, decision_id=None, profile_overlay_id=None):
    from app.services.diagnosis import Incident, DiagnosisState, get_diagnoser
    from app.services.observation import ExecutionControl, ObservationEvent, digest, get_observer, business_snapshot
    from app.services.step_failure_policy import MAX_TOTAL_FAILS, is_sleeping
    from app.services.step_cancel import is_generation_active, stop_flag_path
    from app.project_db import project_db_session_scope, push_runtime_to_master
    from app.services.node_status_machine import start_node_running
    if not all(str(v).strip() for v in (idempotency_key, initiator, rationale)):
        raise ValueError("explicit recovery identity and rationale required")
    observer = get_observer()
    async with supervisor.master() as master:
        prior = await master.scalar(select(RecoveryAction).where(
            RecoveryAction.execution_id == execution_id, RecoveryAction.idempotency_key == idempotency_key))
        if prior:
            if prior.project_id != project_id or prior.node_key != node_key or prior.incident_id != incident_id:
                raise ValueError("idempotency identity conflict")
            return {**ticket_json(prior), "idempotent_replay": True}
        prior_decision = await master.scalar(select(ActionRecord).where(
            ActionRecord.execution_id == execution_id, ActionRecord.idempotency_key == idempotency_key))
        if prior_decision:
            raise ValueError("idempotency key has a prior decision; no redispatch")
        from app.services import kill_switch
        if await kill_switch.is_active(master):
            raise ValueError("kill switch active: operator emergency stop; no recovery reserved or dispatched")
        from app.services.decision_state import authorize_recovery
        await authorize_recovery(master, execution_id, incident_id, decision_id)
        incident = await master.get(Incident, incident_id)
        from app.services import media_admission
        media = bool(incident is not None and media_admission.is_media_incident(incident))
        if media and profile_overlay_id:
            raise ValueError("media recovery has no provider-profile strategy")
        if (incident is None or incident.execution_id != execution_id or incident.project_id != project_id
                or incident.node_key != node_key
                or (incident.type not in {"invalid_json", "missing_payload"} and not media)
                or incident.status == "resolved_confirmed" or not incident.details.get("currently_relevant")
                or (incident.details.get("evidence_incomplete") and not media)):
            raise ValueError("current mandatory JSON/payload incident required")
        scope = await master.get(ExecutionControl, execution_id)
        control = await master.get(ActionExecutionControl, execution_id)
        if not control or control.project_id != project_id or not control.enabled or control.held:
            raise ValueError("managed execution disabled or held")
        if node_key not in control.node_keys:
            raise ValueError("node outside recovery scope")
        from app.services.managed_launch import allowed_actions
        action_type = 'retry_profile' if profile_overlay_id else 'retry_same'
        if action_type not in await allowed_actions(
                master,execution_id,{'retry_same','retry_profile'}):
            raise ValueError('recovery strategy outside execution grant')
        from app.services.decision_config import DecisionRuntimeBinding,runtime_config,check_incident_scope
        cfg=runtime_config()
        binding=await master.get(DecisionRuntimeBinding,execution_id)
        if cfg.get('incident_scope') or (binding and binding.config.get('incident_scope')):
            if not binding:raise ValueError('pilot recovery requires a bound runtime configuration')
            if binding.config_hash!=digest(cfg):raise ValueError('runtime configuration revision changed')
            check_incident_scope(cfg,execution_id=execution_id,incident_id=incident_id,
                node_key=node_key,source_attempt_id=incident.attempt_id)
            if action_type not in cfg['allowed_actions']:
                raise ValueError('action outside pilot allowlist')
            consumed=await master.scalar(select(RecoveryAction.action_id).where(RecoveryAction.execution_id==execution_id))
            if consumed:raise ValueError('single pilot recovery already consumed')
        if (not scope or scope.observer_state != "active" or scope.gaps
                or execution_id in observer.failures or execution_id in observer.commit_uncertainty
                or get_diagnoser(observer).failures):
            raise ValueError("audit/diagnosis unavailable")
        blockers = (await master.scalars(select(Incident).where(Incident.execution_id == execution_id,
            Incident.type.in_(["user_stop", "uncertain_request", "journal_gap", "stalled"])))).all()
        # A media uncertain_request is judged by media_recovery.assess (ledger facts), not as a blanket stop.
        if any(i.status != "resolved_confirmed" and i.details.get("currently_relevant")
               and not (media and media_admission.is_media_incident(i)) for i in blockers):
            raise ValueError("uncertain request, user_stop or audit/state blocker")
        state = await master.get(DiagnosisState, execution_id)
        events = (await master.scalars(select(ObservationEvent).where(
            ObservationEvent.execution_id == execution_id).order_by(ObservationEvent.id))).all()
        if not state or not events or state.cursor < events[-1].id:
            raise ValueError("diagnosis is stale")
        run = await master.get(WorkflowRun, control.workflow_run_id)
        if not run:
            raise ValueError("graph revision changed")
        graph_problem = revisions.graph_mismatch_reason(
            run.nodes_snapshot, run.edges_snapshot, control.graph_version)
        if graph_problem:
            raise ValueError(graph_problem)
        from app.services.excel_gpt_node import effective_node_type
        from app.services.node_capabilities import capability_for
        # Execution grants may cover mixed families. The immutable graph retains
        # scene markers that a runtime NodeRun projection can normalize away.
        graph_node = next((n for n in (run.nodes_snapshot or [])
                           if isinstance(n, dict) and str(n.get("id")) == node_key), None)
        if graph_node is None:
            raise ValueError("recovery node capability unavailable in workflow snapshot")
        node_type = effective_node_type(graph_node)
        capability = capability_for(node_type)
        if media and not media_admission.admitted(node_type):
            raise media_admission.MediaAdmissionDenied("node_not_admitted", node_type)
        if not media and node_type in media_admission.ADMITTED_NODES:
            raise ValueError(f"recovery of {node_type} is admitted for media incidents only")
        if not capability.managed or action_type not in capability.actions:
            raise ValueError(f"recovery strategy {action_type} denied by node capability: {node_type}")
        attempts = [e for e in events if e.node_key == node_key and e.kind == "node_start"]
        source = attempts[-1] if attempts else None
        if not source or source.attempt_id != incident.attempt_id:
            raise ValueError("incident attempt is stale")
        facts = [e for e in events if e.node_key == node_key and e.attempt_id == source.attempt_id]
        starts = [e for e in facts if e.kind == "model_start"]
        ends = {e.request_id for e in facts if e.kind == "model_end" and e.payload.get("complete")}
        if media:
            # No model request exists for a media node: the failed attempt itself is the evidence; the
            # provider-side facts come from the ledger (media_recovery.assess) before the ticket is reserved.
            if not any(e.kind == "node_error" for e in facts):
                raise ValueError("uncertain request, unfinished attempt or unconfirmed partial apply")
        elif (not starts or any(e.request_id not in ends for e in starts)
                or not any(e.kind == "node_error" for e in facts)
                or any(e.kind in {"data_applied", "apply_input", "apply_error"} for e in facts)):
            raise ValueError("uncertain request, unfinished attempt or unconfirmed partial apply")
        # Only executors that emit an inner `json_retry` fact must show it. Native text
        # nodes have no inner JSON retry; their exhaustion is proven by the runtime
        # failure counter and abandonment check further below (all native retries).
        if not media and capability.native_json_retry and not any(e.kind == "json_retry" for e in facts):
            raise ValueError("native JSON retries not exhausted")
        if control.requests_used >= control.request_limit:
            raise ValueError("supervisor request budget exhausted")
        existing = await master.scalar(select(RecoveryAction).where(
            RecoveryAction.execution_id == execution_id, RecoveryAction.node_key == node_key))
        if existing:
            raise ValueError("single recovery already consumed")
        async with project_db_session_scope(project_id) as session:
            project = await session.get(Project, project_id)
            master_project = await master.get(Project, project_id)
            if (not project or not master_project or (project.meta or {}).get("user_stop")
                    or (master_project.meta or {}).get("user_stop") or stop_flag_path(project_id).exists()):
                raise ValueError("user_stop is active")
            primary_snapshot, _ = await business_snapshot(master, scope)
            replica_snapshot, _ = await business_snapshot(session, scope)
            if (primary_snapshot["nodes"] != replica_snapshot["nodes"]
                    or primary_snapshot["project_status"] != replica_snapshot["project_status"]):
                raise ValueError("master/project node or project state disagreement")
            if is_generation_active(project_id):
                raise ValueError("previous worker is still active")
            fs = (project.meta or {}).get("step_failure") or {}
            running = fs.get("last_running")
            if (not fs.get("abandoned_at") or (fs.get("total_fails") or {}).get(running, 0) < MAX_TOTAL_FAILS
                    or is_sleeping(project) or project.status != ProjectStatus.paused):
                raise ValueError("all native runtime retries must be exhausted")
            nodes = (await session.scalars(select(NodeRun).where(
                NodeRun.workflow_run_id == control.workflow_run_id))).all()
            node = next((n for n in nodes if n.node_key == node_key), None)
            if not node or node.status != NodeRunStatus.failed or not node.finished_at:
                raise ValueError("previous node attempt is not terminal")
            if any(n.status in {NodeRunStatus.running, NodeRunStatus.queued, NodeRunStatus.waiting_hitl} for n in nodes):
                raise ValueError("another node or HITL is active")
            replica = await session.get(WorkflowRun, control.workflow_run_id)
            if not replica:
                raise ValueError("project graph unavailable")
            current_parts = {}
            revision = await input_revision(session, project, node_key, parts=current_parts)
            previous_parts = source.payload.get("recovery_revision_parts") or {}
            scheme_problem = revisions.stored_revision_problem(previous_parts) if previous_parts else None
            if scheme_problem:
                raise ValueError(scheme_problem)
            parts_match = bool(previous_parts) and all(previous_parts.get(k) == v for k,v in current_parts.items())
            if not parts_match and source.payload.get("recovery_input_revision") != revision:
                changed = [k for k,v in current_parts.items() if previous_parts.get(k) != v]
                raise ValueError("input/config revision changed or not recorded"
                                 + ("" if previous_parts else " (no parts recorded: legacy scheme 1 or missing)")
                                 + ": " + ",".join(changed))
            admission = None
            if media:
                # Read-only ledger/file assessment BEFORE any ticket exists: a refusal leaves no trace to consume.
                admission = await media_admission.admit(project, node_type)
            profile = None
            if profile_overlay_id:
                from app.services.profiles_v1 import reservation, source_provider
                profile = await reservation(master, profile_overlay_id, execution_id=execution_id,
                    project_id=project_id,node_key=node_key,source_attempt_id=source.attempt_id,
                    revision=revision,graph=control.graph_version,body_hash=starts[0].payload["input_revision"],
                    source_provider=source_provider(starts[0].payload))
            action = RecoveryAction(action_id=str(uuid.uuid4()), execution_id=execution_id,
                project_id=project_id, incident_id=incident_id, node_key=node_key,
                source_attempt_id=source.attempt_id, new_attempt_id=str(uuid.uuid4()),
                idempotency_key=idempotency_key, preconditions={"native_failures": fs["total_fails"][running],
                    "running_status": running, "input_revision": revision, "graph_version": control.graph_version,
                    "action_type": action_type, "effective_node_type": node_type,
                    "capability_actions": list(capability.actions),
                    "input_parts": current_parts, "revision_scheme": revisions.REVISION_SCHEME,
                    "node_config_revision": current_parts.get("node_config_revision"),
                    "graph_authority": "master revision + runtime canvas_graph",
                    "first_body_hash": starts[0].payload["input_revision"] if starts else None,
                    "prior_attempt_terminal": True,
                    "node_and_project_replicas_match": True,
                    "workflow_aggregate_status": {"master": primary_snapshot["workflow_status"],
                        "project": replica_snapshot["workflow_status"],
                        "note": "run_sync derives aggregate; project_db copies NodeRuns, not WorkflowRun.status"},
                    "no_partial_apply": True, "user_stop": False, "node_scope_authorized": True,
                    **({"media": True, "media_operation": admission["operation"],
                        "media_verdict": {k: admission["verdict"].get(k) for k in ("mode", "code", "reasons")},
                        "submits_new_paid_request": False} if media else {})},
                budget_state={"request_limit": control.request_limit, "requests_used": control.requests_used,
                    "recovery_limit": 1, "recoveries_reserved": 1}, status="reserved", result={}, verification_result={})
            if profile:
                action.preconditions = {**action.preconditions,
                    "profile_overlay_id":profile.overlay_id,"candidate_hash":profile.candidate_hash,
                    "source_body_hash":profile.source_body_hash,"first_body_hash":profile.expected_body_hash}
                profile.attempt_id=action.new_attempt_id;profile.status="reserved"
                profile.history=[*profile.history,{"kind":"recovery_reserved","action_id":action.action_id,
                    "attempt_id":action.new_attempt_id,"at":datetime.utcnow().isoformat()}]
            if capability.volatile_attachment_names:
                # Compare the first recovery request with the source request modulo the
                # attachment file-name stamps (profile: the approved transformation of it).
                source_body = load_source_body(observer, starts[0])
                if profile:
                    from app.services.profiles_v1 import effective_body
                    source_body = effective_body(source_body, profile.candidate)
                action.preconditions = {**action.preconditions,
                    "first_body_hash": body_identity(source_body, normalize_names=True),
                    "first_body_normalization": STAMP_NORMALIZATION}
            master.add(action)
            master.add(ActionRecord(action_id=action.action_id, execution_id=execution_id, project_id=project_id,
                action_kind="node_profile_recovery" if profile else ("media_recovery" if media else "node_recovery"),
                node_key=node_key, attempt_id=action.new_attempt_id,
                initiator=initiator, rationale=rationale, evidence_ids=incident.details.get("event_ids", []),
                preconditions=action.preconditions, idempotency_key=idempotency_key, status="reserved", verification={}))
            await master.commit()  # Durable before dispatch; UNIQUE prevents races.
            _active_nodes[project_id] = node_key
            record = await master.get(ActionRecord, action.action_id)
            if media:
                # Never a worker dispatch: the resume runs here, inside resume_only, and the ticket is final.
                return await _execute_media(master, scope, control, action, record, project, node_type,
                                            admission, project_id)
            action.status = "queued"
            record.status = "queued"
            await master.commit()
            # Preserve attempts, stop, sleep and native failure counters. No start_step.
            if not start_node_running(node, project_id=project_id, initiator="supervisor_recovery"):
                action.status = "human_review"
                action.result = {"dispatch_refused": True, "new_attempt_started": False}
                action.verification_result = {"verified": False, "reason": "FSM/permission changed before dispatch"}
                record.status = "rejected"
                record.verification = action.verification_result
                control.held = True
                await master.commit()
                update_permission(project_id, held=True)
                raise ValueError("FSM refused recovery dispatch")
            project.status = ProjectStatus(running)
            await session.flush()
            await push_runtime_to_master(session, project)
        return {**ticket_json(action), "idempotent_replay": False}


async def _execute_media(master, scope, control, action, record, project, node_type, admission, project_id):
    """Run the admitted resume operation and settle the ticket from independently re-read facts.

    claimed -> verified | human_review. Not repeated on restart (reconcile holds an interrupted one)."""
    from app.services import media_admission
    from app.services.observation import get_observer
    action.status = "claimed"
    record.status = "running"
    action.result = {"media_operation": admission["operation"], "worker_dispatched": False,
                     "claimed_at": datetime.utcnow().isoformat()}
    await master.commit()
    outcome = await media_admission.run_and_verify(project, node_type, admission)
    verified = bool(outcome["verified"])
    action.result = {**action.result, "worker_returned": False, "new_paid_requests": outcome["new_paid_requests"],
                     "continuation": "none; the node is continued by a human with the ordinary start"}
    action.verification_result = {**outcome, "verified": verified}
    action.status = "verified" if verified else "human_review"
    record.status = action.status
    record.verification = action.verification_result
    if not verified:
        control.held = True
    try:
        get_observer().stage(master, scope, "recovery_verification", db_scope="master", node_key=action.node_key,
                             attempt_id=action.new_attempt_id,
                             payload={"action_id": action.action_id, "media": True, **action.verification_result})
    except Exception:  # noqa: BLE001 - the ticket row itself is the durable verdict
        pass
    await master.commit()
    if verified:
        _active_nodes.pop(project_id, None)
    else:
        update_permission(project_id, held=True)
    return {**ticket_json(action), "idempotent_replay": False}


async def claim(observer, session, project, node_key, attempt_id, *, execution_id):
    """CAS consumes the ticket before the original worker executes the node."""
    from app.services.observation import digest
    from app.services.step_cancel import stop_flag_path
    async with observer.master() as master:
        row = await master.scalar(select(RecoveryAction).where(
            RecoveryAction.execution_id == execution_id,
            RecoveryAction.project_id == project.id, RecoveryAction.node_key == node_key,
            RecoveryAction.status.in_(["reserved", "queued", "claimed", "await_verify", "human_review"])))
        if not row:
            return attempt_id
        if row.status != "queued":
            raise RuntimeError("recovery already consumed or dispatch uncertain; human review required")
        if (project.meta or {}).get("user_stop") or stop_flag_path(project.id).exists():
            raise RuntimeError("user_stop prevents recovery claim")
        control = await master.get(ActionExecutionControl, row.execution_id)
        if not control or not control.enabled or control.held:
            raise RuntimeError("recovery permission revoked or held")
        run = await session.get(WorkflowRun, control.workflow_run_id)
        master_run = await master.get(WorkflowRun, control.workflow_run_id)
        stored_parts = row.preconditions.get("input_parts")
        scheme_problem = revisions.stored_revision_problem(stored_parts) if stored_parts else None
        if scheme_problem:
            raise RuntimeError("recovery ticket rejected before claim: " + scheme_problem)
        if run and master_run:
            graph_problem = revisions.graph_mismatch_reason(
                master_run.nodes_snapshot, master_run.edges_snapshot, row.preconditions["graph_version"])
            if graph_problem and "legacy scheme" in graph_problem:
                raise RuntimeError("recovery ticket rejected before claim: " + graph_problem)
        if (not run or not master_run
                or revisions.graph_version(master_run.nodes_snapshot, master_run.edges_snapshot) != row.preconditions["graph_version"]
                or await input_revision(session, project, node_key) != row.preconditions["input_revision"]):
            raise RuntimeError("recovery revisions changed before claim")
        # Historical same tickets lack action_type; profile tickets retain both
        # their strategy and overlay marker. Capability proof is never authority.
        action_type = row.preconditions.get("action_type")
        has_profile_marker = "profile_overlay_id" in row.preconditions
        if action_type is None and not has_profile_marker:
            action_type = "retry_same"
        if (action_type not in {"retry_same", "retry_profile"}
                or (action_type == "retry_profile") != has_profile_marker
                or (has_profile_marker and not row.preconditions["profile_overlay_id"])):
            raise RuntimeError("recovery strategy/profile metadata invalid before claim")
        from app.services.managed_launch import allowed_actions
        if (node_key not in control.node_keys or action_type not in await allowed_actions(
                master, row.execution_id, {"retry_same", "retry_profile"})):
            raise RuntimeError("recovery strategy/node outside current execution grant before claim")
        from app.services.excel_gpt_node import effective_node_type
        from app.services.node_capabilities import capability_for
        graph_node = next((n for n in (master_run.nodes_snapshot or [])
                           if isinstance(n, dict) and str(n.get("id")) == node_key), None)
        if graph_node is None:
            raise RuntimeError("recovery node capability unavailable before claim")
        node_type = effective_node_type(graph_node)
        capability = capability_for(node_type)
        if not capability.managed or action_type not in capability.actions:
            raise RuntimeError(f"recovery strategy {action_type} denied by node capability before claim: {node_type}")
        result = await master.execute(update(RecoveryAction).where(
            RecoveryAction.action_id == row.action_id, RecoveryAction.status == "queued")
            .values(status="claimed", result={"worker_claimed": True, "claimed_at": datetime.utcnow().isoformat()}))
        if result.rowcount != 1:
            raise RuntimeError("recovery ticket concurrently consumed")
        record = await master.get(ActionRecord, row.action_id)
        record.status = "running"
        await master.commit()
        _inflight_projects.add(project.id)
        return row.new_attempt_id


async def check_body(ctx, body_hash, body=None):
    async with ctx.observer.master() as session:
        row = await session.scalar(select(RecoveryAction).where(
            RecoveryAction.execution_id == ctx.scope.execution_id,
            RecoveryAction.new_attempt_id == ctx.attempt_id))
        if row and (ctx.scope.observer_state != "active" or ctx.scope.gaps
                    or ctx.scope.execution_id in ctx.observer.failures
                    or ctx.scope.execution_id in ctx.observer.commit_uncertainty):
            raise RuntimeError("recovery audit unavailable; blocked before transport")
        if row and ctx.last_request_id is None:
            if row.preconditions.get("first_body_normalization") == STAMP_NORMALIZATION:
                observed = body_identity(body, normalize_names=True) if body is not None else None
            else:
                observed = body_hash
            if observed != row.preconditions["first_body_hash"]:
                raise RuntimeError("recovery HTTP input changed; blocked before transport")


async def worker_finished(ctx, error=False):
    async with ctx.observer.master() as session:
        row = await session.scalar(select(RecoveryAction).where(
            RecoveryAction.execution_id == ctx.scope.execution_id,
            RecoveryAction.new_attempt_id == ctx.attempt_id))
        if not row:
            return
        row.status = "human_review" if error else "await_verify"
        row.result = {**row.result, "worker_returned": not error, "worker_error": error,
                      "worker_returned_at": datetime.utcnow().isoformat(),
                      # Retain the configuration captured at attempt entry. Reading
                      # runtime_config here would silently authorize a later change.
                      "runtime_config_hash": ctx.runtime_config_hash}
        control = await session.get(ActionExecutionControl, row.execution_id)
        row.budget_state = {**row.budget_state, "requests_used_after_attempt": control.requests_used}
        record = await session.get(ActionRecord, row.action_id)
        record.status = row.status
        if error:
            row.verification_result = {"verified": False, "worker_error": True,
                "reason": "single recovery failed; human review required"}
            record.verification = row.verification_result
            control = await session.get(ActionExecutionControl, row.execution_id)
            control.held = True
            update_permission(row.project_id, held=True)
        await session.commit()


def defer_continuation(project_id, node_key):
    from app.services.observation import _attempt
    ctx = _attempt.get()
    return bool(ctx and ctx.session.info.get("recovery_attempt_id") == ctx.attempt_id
                and ctx.scope.project_id == project_id and ctx.node_key == node_key)


async def reconcile(observer):
    """After business commit, verify matching attempt then use original continuation.

    A claimed ticket without a confirmed end after worker exit is uncertain;
    recovery itself is never re-enqueued on restart.
    """
    from app.services.observation import ObservationEvent, ExecutionControl, digest, AttemptContext, _attempt, emit
    from app.services.step_cancel import is_generation_active, stop_flag_path
    from app.project_db import project_db_session_scope, push_runtime_to_master, pull_master_runtime_into_project
    async with observer.master() as master:
        rows = (await master.scalars(select(RecoveryAction).where(
            RecoveryAction.status.in_(["reserved", "queued", "claimed", "await_verify"])))).all()
        for row in rows:
            if (row.preconditions or {}).get("media"):
                from app.services import media_admission
                control = await master.get(ActionExecutionControl, row.execution_id)
                if await media_admission.settle_interrupted(master, row, control, observer, update_permission):
                    await master.commit()
                continue
            if row.status == "reserved" and (datetime.utcnow() - row.created_at).total_seconds() < 30:
                continue
            if row.status == "claimed" and (datetime.utcnow() - row.created_at).total_seconds() < 30:
                continue
            if row.status == "queued" and (datetime.utcnow() - row.created_at).total_seconds() < 120:
                continue
            if row.project_id in _inflight_projects or is_generation_active(row.project_id):
                continue
            control = await master.get(ActionExecutionControl, row.execution_id)
            scope = await master.get(ExecutionControl, row.execution_id)
            facts = (await master.scalars(select(ObservationEvent).where(
                ObservationEvent.execution_id == row.execution_id,
                ObservationEvent.attempt_id == row.new_attempt_id))).all()
            async with project_db_session_scope(row.project_id) as session:
                project = await session.get(Project, row.project_id)
                if project:
                    await pull_master_runtime_into_project(session, project)
                run = await session.get(WorkflowRun, control.workflow_run_id)
                master_run = await master.get(WorkflowRun, control.workflow_run_id)
                node = await session.scalar(select(NodeRun).where(
                    NodeRun.workflow_run_id == control.workflow_run_id, NodeRun.node_key == row.node_key))
                verification = {
                    "node_done": bool(node and node.status == NodeRunStatus.done),
                    "node_end": any(e.kind == "node_end" for e in facts),
                    "data_applied": any(e.kind == "data_applied" for e in facts),
                    "accepted_payload": any(e.kind == "parse_result" and e.payload.get("payload_present") for e in facts),
                    "graph_matches": bool(run and master_run and revisions.graph_version(
                        master_run.nodes_snapshot, master_run.edges_snapshot) == control.graph_version),
                    "audit_available": not bool(scope.gaps or row.execution_id in observer.failures
                                                or row.execution_id in observer.commit_uncertainty),
                    "no_user_stop": bool(project and not (project.meta or {}).get("user_stop")
                                         and not stop_flag_path(row.project_id).exists()),
                    "permission_active": control.enabled and not control.held,
                    "requests_complete": all(any(end.kind == "model_end" and end.request_id == e.request_id
                        and end.payload.get("complete") for end in facts) for e in facts if e.kind == "model_start"),
                    "transactional_done": any(e.kind == "node_change" and e.db_scope == "project"
                        and "done" in ((e.payload.get("changes") or {}).get("status") or {}).get("after", []) for e in facts),
                }
                if not verification["transactional_done"] and master_run is not None:
                    from app.services.excel_gpt_node import effective_node_type
                    from app.services.node_capabilities import capability_for
                    boundary_node = next((n for n in (master_run.nodes_snapshot or [])
                                          if isinstance(n, dict) and str(n.get("id")) == row.node_key), None)
                    own_start = min((e.id for e in facts if e.kind == "node_start"), default=None)
                    if (boundary_node is not None and own_start is not None
                            and capability_for(effective_node_type(boundary_node)).recovery_continuation == "none"):
                        # Native text nodes: the runtime commits the NodeRun done transition right
                        # after the executor returns, outside the attempt context (no attempt id).
                        # Accept that committed project-scope transition only when it follows THIS
                        # attempt's start and no other attempt of the node started in between. The
                        # mandatory HITL request stays pending; nothing here approves it.
                        later = (await master.scalars(select(ObservationEvent).where(
                            ObservationEvent.execution_id == row.execution_id,
                            ObservationEvent.node_key == row.node_key, ObservationEvent.id > own_start,
                            ObservationEvent.kind.in_(["node_change", "node_start"])).order_by(ObservationEvent.id))).all()
                        other_start = any(e.kind == "node_start" and e.attempt_id != row.new_attempt_id for e in later)
                        verification["transactional_done"] = bool(not other_start and any(
                            e.kind == "node_change" and e.db_scope == "project"
                            and "done" in ((e.payload.get("changes") or {}).get("status") or {}).get("after", [])
                            for e in later))
                        verification["done_proof"] = "native text node: committed NodeRun done after this attempt's start"
                from app.services.observation import confirmed_qc_noop
                from app.services.node_write_contract import allows_qc_noop
                from app.services.gpt_operator import operator_config
                cfg = operator_config(project, row.node_key) if project else {}
                variant = (((project.meta or {}).get('prompt_slot_variants') or {}).get(row.node_key) or {}).get('main') if project else None
                trusted = bool(project and allows_qc_noop(project, row.node_key, variant=variant,
                    output_mode=cfg.get('outputMode'), check_mode=cfg.get('checkMode')))
                latest_start = await master.scalar(select(ObservationEvent).where(
                    ObservationEvent.execution_id == row.execution_id,
                    ObservationEvent.node_key == row.node_key,
                    ObservationEvent.kind == 'node_start').order_by(ObservationEvent.id.desc()).limit(1))
                events = [dict(kind=e.kind, request_id=e.request_id, payload=e.payload) for e in facts]
                verification['accepted_noop'] = bool(trusted and latest_start
                    and latest_start.attempt_id == row.new_attempt_id
                    and any(e.kind == 'node_change' and e.db_scope == 'project'
                        and e.payload.get('transactional') is True
                        and 'done' in ((e.payload.get('changes') or {}).get('status') or {}).get('after', []) for e in facts)
                    and any(confirmed_qc_noop(events, e.request_id) for e in facts if e.kind == 'parse_result'))
                verification['result_confirmed'] = ((verification['data_applied'] and verification['accepted_payload'])
                                                   or verification['accepted_noop'])
                required_checks = ('node_done','node_end','result_confirmed','graph_matches','audit_available',
                                   'no_user_stop','permission_active','requests_complete','transactional_done')
                verified = row.status == "await_verify" and all(verification[k] for k in required_checks)
                completion_checks = ('node_done','node_end','result_confirmed',
                                     'requests_complete','transactional_done')
                pending = [key for key in completion_checks if not verification[key]]
                finished_at = row.result.get('worker_returned_at')
                age = (datetime.utcnow()-datetime.fromisoformat(finished_at)).total_seconds() if finished_at else 999
                hard_checks = ('graph_matches','audit_available','no_user_stop','permission_active')
                if (row.status == 'await_verify' and pending and age < 30
                        and all(verification[k] for k in hard_checks)):
                    row.verification_result = {**verification,'verified':False,'waiting_for_evidence':pending}
                    wait_key = 'recovery-wait:'+row.action_id+':'+digest(pending)[:12]
                    prior = await master.scalar(select(ActionRecord).where(ActionRecord.execution_id==row.execution_id,
                        ActionRecord.idempotency_key==wait_key))
                    if not prior:
                        master.add(ActionRecord(action_id=str(uuid.uuid4()),execution_id=row.execution_id,
                            project_id=row.project_id,action_kind='verification_wait',node_key=row.node_key,
                            attempt_id=row.new_attempt_id,initiator='supervisor',rationale='Wait for durable completion event delivery',
                            idempotency_key=wait_key,status='await_verify',preconditions={'pending_evidence':pending},
                            evidence_ids=[],verification=row.verification_result))
                    await master.commit()
                    continue
                row.verification_result = {**verification, "verified": verified, "semantic_quality_verified": False}
                row.status = "verified" if verified else "human_review"
                record = await master.get(ActionRecord, row.action_id)
                record.status = row.status
                record.verification = row.verification_result
                if not verified:
                    control.held = True
                    update_permission(row.project_id, held=True)
                observer.stage(master, scope, 'recovery_verification', db_scope='master',node_key=row.node_key,
                    attempt_id=row.new_attempt_id,payload={'action_id':row.action_id,**row.verification_result})
                # Persist decision BEFORE continuation. No retry if continuation fails.
                await master.commit()
                if verified:
                    _active_nodes.pop(row.project_id, None)
                    from app.services.excel_gpt_node import slot_index_from_node, effective_node_type
                    from app.services.node_capabilities import capability_for
                    from app.orchestrator.steps.enrich_xlsx import _after_excel_gpt_done
                    graph_node = next(n for n in run.nodes_snapshot if str(n.get("id")) == row.node_key)
                    if capability_for(effective_node_type(graph_node)).recovery_continuation != "excel_gpt_chain":
                        # Native text nodes: the executor already ran its own post-step
                        # (storage sync, mandatory HITL request). The supervisor adds no
                        # continuation and never approves a HITL; the original runtime
                        # proceeds only after the human decision.
                        await push_runtime_to_master(session, project)
                        row.result = {**row.result, "continuation_submitted": False,
                                      "continuation": "original runtime; mandatory HITL untouched"}
                        await master.commit()
                        continue
                    # Reconcile can run after the worker context/process is gone.
                    # Old tickets without proof remain blocked by the media gate;
                    # never backfill a missing hash from the current configuration.
                    token = _attempt.set(AttemptContext(observer,scope,session,'project',row.node_key,row.new_attempt_id,
                        runtime_config_hash=row.result.get("runtime_config_hash")))
                    try:
                        await _after_excel_gpt_done(session, project, node_key=row.node_key,
                            slot_idx=slot_index_from_node(graph_node), ready_status=project.status)
                        emit('recovery_continuation_submitted', {'action_id':row.action_id,
                            'next_node_key':(project.meta or {}).get('active_excel_gpt_node_key'),
                            'initiator':'supervisor','planner':'original_runtime'})
                    except BaseException as exc:
                        row.result = {**row.result,'continuation_error_type':type(exc).__name__,
                            'continuation_unconfirmed':True}
                        row.status = 'human_review'
                        record.status = 'human_review'
                        control.held = True
                        await master.commit()
                        update_permission(row.project_id,held=True)
                        raise
                    finally:
                        _attempt.reset(token)
                    await push_runtime_to_master(session, project)
                    row.result = {**row.result, "continuation_submitted": True}
            await master.commit()
