"""One-shot recovery tickets for the existing worker, never manual run_step.

Reservation precedes dispatch; claim precedes HTTP. Ambiguous dispatch/claim is
held for a human, never replayed. Continuation uses the original graph planner.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import JSON, String, UniqueConstraint, select, update
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, NodeRun, NodeRunStatus, Project, ProjectStatus, WorkflowRun
from app.services.actions_v1 import ActionExecutionControl, ActionRecord, update_permission

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


async def input_revision(session, project, node_key, *, parts=None):
    """Only hashes leave the process. Runtime bookkeeping is not node input.

    In addition, the first recovery HTTP body must equal the source body hash.
    This catches file-derived inputs not represented in domain tables.
    """
    from app.models import Frame, Scene, FrameText, SceneDesignCell
    from app.services.observation import digest
    from app.settings import settings
    columns = ("topic", "general_plan", "hero_description", "script_text", "hero_mode",
               "hero_descriptions", "item_descriptions", "prompt_overrides", "gpt_text_overrides")
    state = {k: getattr(project, k) for k in columns}
    for model in (Frame, Scene, FrameText, SceneDesignCell):
        rows = (await session.scalars(select(model).where(model.project_id == project.id)
                                      .order_by(model.id))).all()
        state[model.__tablename__] = [
            {c.name: getattr(row, c.name) for c in model.__table__.columns
             if c.name not in {"created_at", "updated_at"}} for row in rows]
    meta = project.meta or {}
    # The canvas includes per-node model, prompt, source and write contracts.
    state["canvas"] = {k: v for k, v in meta.items() if "canvas" in k or k == "workflow_graph"}
    # The runtime reads canvas_graph. WorkflowRun in project.db is a projection
    # that _prepare_node_run_for_status normalizes during the failed attempt.
    # Master graph is checked independently against the enabled revision.
    state["settings"] = settings.model_dump()
    prompts = Path(__file__).resolve().parents[2] / "prompts"
    state["prompts"] = {str(p.relative_to(prompts)): digest(p.read_bytes().hex())
                        for p in sorted(prompts.rglob("*.md"))}
    source_root = Path(__file__).resolve().parents[2]
    node_code = ('app/orchestrator/steps/enrich_xlsx.py','app/orchestrator/pipeline.py',
        'app/services/gpt_client.py','app/services/db_apply.py','app/services/node_write_contract.py',
        'app/services/gpt_text_builder.py')
    state["node_code"] = {name:digest((source_root/name).read_bytes().hex()) for name in node_code}
    if parts is not None:
        parts.update({k:digest(v) for k,v in state.items()})
    return digest(state)


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
        from app.services.decision_state import authorize_recovery
        await authorize_recovery(master, execution_id, incident_id, decision_id)
        incident = await master.get(Incident, incident_id)
        if (incident is None or incident.execution_id != execution_id or incident.project_id != project_id
                or incident.node_key != node_key or incident.type not in {"invalid_json", "missing_payload"}
                or incident.status == "resolved_confirmed" or not incident.details.get("currently_relevant")
                or incident.details.get("evidence_incomplete")):
            raise ValueError("current mandatory JSON/payload incident required")
        scope = await master.get(ExecutionControl, execution_id)
        control = await master.get(ActionExecutionControl, execution_id)
        if not control or control.project_id != project_id or not control.enabled or control.held:
            raise ValueError("managed execution disabled or held")
        if node_key not in control.node_keys:
            raise ValueError("node outside recovery scope")
        if (not scope or scope.observer_state != "active" or scope.gaps
                or execution_id in observer.failures or execution_id in observer.commit_uncertainty
                or get_diagnoser(observer).failures):
            raise ValueError("audit/diagnosis unavailable")
        blockers = (await master.scalars(select(Incident).where(Incident.execution_id == execution_id,
            Incident.type.in_(["user_stop", "uncertain_request", "journal_gap"])))).all()
        if any(i.status != "resolved_confirmed" and i.details.get("currently_relevant") for i in blockers):
            raise ValueError("uncertain request, user_stop or audit/state blocker")
        state = await master.get(DiagnosisState, execution_id)
        events = (await master.scalars(select(ObservationEvent).where(
            ObservationEvent.execution_id == execution_id).order_by(ObservationEvent.id))).all()
        if not state or not events or state.cursor < events[-1].id:
            raise ValueError("diagnosis is stale")
        run = await master.get(WorkflowRun, control.workflow_run_id)
        if not run or digest({"nodes": run.nodes_snapshot, "edges": run.edges_snapshot}) != control.graph_version:
            raise ValueError("graph revision changed")
        attempts = [e for e in events if e.node_key == node_key and e.kind == "node_start"]
        source = attempts[-1] if attempts else None
        if not source or source.attempt_id != incident.attempt_id:
            raise ValueError("incident attempt is stale")
        facts = [e for e in events if e.node_key == node_key and e.attempt_id == source.attempt_id]
        starts = [e for e in facts if e.kind == "model_start"]
        ends = {e.request_id for e in facts if e.kind == "model_end" and e.payload.get("complete")}
        if (not starts or any(e.request_id not in ends for e in starts)
                or not any(e.kind == "node_error" for e in facts)
                or any(e.kind in {"data_applied", "apply_input", "apply_error"} for e in facts)):
            raise ValueError("uncertain request, unfinished attempt or unconfirmed partial apply")
        if not any(e.kind == "json_retry" for e in facts):
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
            parts_match = bool(previous_parts) and all(previous_parts.get(k) == v for k,v in current_parts.items())
            if not parts_match and source.payload.get("recovery_input_revision") != revision:
                changed = [k for k,v in current_parts.items() if previous_parts.get(k) != v]
                raise ValueError("input/config revision changed or not recorded: " + ",".join(changed))
            profile = None
            if profile_overlay_id:
                from app.services.profiles_v1 import reservation
                profile = await reservation(master, profile_overlay_id, execution_id=execution_id,
                    project_id=project_id,node_key=node_key,source_attempt_id=source.attempt_id,
                    revision=revision,graph=control.graph_version,body_hash=starts[0].payload["input_revision"])
            action = RecoveryAction(action_id=str(uuid.uuid4()), execution_id=execution_id,
                project_id=project_id, incident_id=incident_id, node_key=node_key,
                source_attempt_id=source.attempt_id, new_attempt_id=str(uuid.uuid4()),
                idempotency_key=idempotency_key, preconditions={"native_failures": fs["total_fails"][running],
                    "running_status": running, "input_revision": revision, "graph_version": control.graph_version,
                    "input_parts": current_parts, "graph_authority": "master revision + runtime canvas_graph",
                    "first_body_hash": starts[0].payload["input_revision"], "prior_attempt_terminal": True,
                    "node_and_project_replicas_match": True,
                    "workflow_aggregate_status": {"master": primary_snapshot["workflow_status"],
                        "project": replica_snapshot["workflow_status"],
                        "note": "run_sync derives aggregate; project_db copies NodeRuns, not WorkflowRun.status"},
                    "no_partial_apply": True, "user_stop": False, "node_scope_authorized": True},
                budget_state={"request_limit": control.request_limit, "requests_used": control.requests_used,
                    "recovery_limit": 1, "recoveries_reserved": 1}, status="reserved", result={}, verification_result={})
            if profile:
                action.preconditions = {**action.preconditions,"action_type":"retry_profile",
                    "profile_overlay_id":profile.overlay_id,"candidate_hash":profile.candidate_hash,
                    "source_body_hash":profile.source_body_hash,"first_body_hash":profile.expected_body_hash}
                profile.attempt_id=action.new_attempt_id;profile.status="reserved"
                profile.history=[*profile.history,{"kind":"recovery_reserved","action_id":action.action_id,
                    "attempt_id":action.new_attempt_id,"at":datetime.utcnow().isoformat()}]
            master.add(action)
            master.add(ActionRecord(action_id=action.action_id, execution_id=execution_id, project_id=project_id,
                action_kind="node_profile_recovery" if profile else "node_recovery", node_key=node_key, attempt_id=action.new_attempt_id,
                initiator=initiator, rationale=rationale, evidence_ids=incident.details.get("event_ids", []),
                preconditions=action.preconditions, idempotency_key=idempotency_key, status="reserved", verification={}))
            await master.commit()  # Durable before dispatch; UNIQUE prevents races.
            _active_nodes[project_id] = node_key
            action.status = "queued"
            record = await master.get(ActionRecord, action.action_id)
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
        if (not run or not master_run or digest({"nodes": master_run.nodes_snapshot, "edges": master_run.edges_snapshot}) != row.preconditions["graph_version"]
                or await input_revision(session, project, node_key) != row.preconditions["input_revision"]):
            raise RuntimeError("recovery revisions changed before claim")
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


async def check_body(ctx, body_hash):
    async with ctx.observer.master() as session:
        row = await session.scalar(select(RecoveryAction).where(
            RecoveryAction.execution_id == ctx.scope.execution_id,
            RecoveryAction.new_attempt_id == ctx.attempt_id))
        if row and (ctx.scope.observer_state != "active" or ctx.scope.gaps
                    or ctx.scope.execution_id in ctx.observer.failures
                    or ctx.scope.execution_id in ctx.observer.commit_uncertainty):
            raise RuntimeError("recovery audit unavailable; blocked before transport")
        if row and ctx.last_request_id is None and body_hash != row.preconditions["first_body_hash"]:
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
                      "worker_returned_at": datetime.utcnow().isoformat()}
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
                    "graph_matches": bool(run and master_run and digest({"nodes": master_run.nodes_snapshot,
                        "edges": master_run.edges_snapshot}) == control.graph_version),
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
                verified = row.status == "await_verify" and all(verification.values())
                completion_checks = ('node_done','node_end','data_applied','accepted_payload',
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
                    from app.services.excel_gpt_node import slot_index_from_node
                    from app.orchestrator.steps.enrich_xlsx import _after_excel_gpt_done
                    graph_node = next(n for n in run.nodes_snapshot if str(n.get("id")) == row.node_key)
                    token = _attempt.set(AttemptContext(observer,scope,session,'project',row.node_key,row.new_attempt_id))
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
