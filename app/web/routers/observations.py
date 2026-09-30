"""Observation history and explicit, policy-gated Actions-v1 controls."""
from __future__ import annotations

from pathlib import Path
import hashlib
import uuid

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models import NodeRun, NodeRunStatus, Project, WorkflowRun
from app.project_db import push_runtime_to_master
from app.services.project_steps import start_step
from app.services.run_sync import sync_run_for_project
from app.web.deps import get_project_session
from app.web.project_dto import project_to_detail
from app.services.event_bus import publish_project_event
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends
from app.services.observation import ExecutionControl, ObservationEvent, digest, get_observer, redact, panel_safe
from app.services.observation import business_snapshot as _business_snapshot
from app.services.actions_v1 import ActionExecutionControl, ActionRecord, ActionSupervisor
from app.web.auth_sessions import AuthDep

router = APIRouter(prefix="/projects/{project_id}/observations", tags=["observations"])


class EnableObservation(BaseModel):
    node_keys: list[str] = Field(min_length=1, max_length=100)


class EnableActions(BaseModel):
    group_id: str = Field(min_length=1, max_length=120)
    node_keys: list[str] = Field(min_length=1, max_length=100)
    rationale: str = Field(min_length=3, max_length=1000)
    request_limit: int = Field(default=12, ge=1, le=100)


class ActionsControl(BaseModel):
    held: bool
    rationale: str = Field(min_length=3, max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=120)


class ManagedStart(BaseModel):
    node_key: str = Field(min_length=1, max_length=60)
    step_code: str = Field(min_length=1, max_length=40)
    rationale: str = Field(min_length=3, max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=120)


class ManagedLaunchRequest(BaseModel):
    model_config = {"extra":"forbid"}
    workflow_run_id: int = Field(ge=1)
    graph_version: str = Field(min_length=64,max_length=64)
    node_keys: list[str] = Field(min_length=1,max_length=100)
    strategies: list[str] = Field(min_length=1,max_length=3)
    request_limit: int = Field(default=12,ge=1,le=100)
    rationale: str = Field(min_length=3,max_length=1000)
    idempotency_key: str = Field(min_length=8,max_length=120)


@router.post('/launch',status_code=201)
async def launch_managed(project_id:int,payload:ManagedLaunchRequest,operator:AuthDep,
                         session:AsyncSession=Depends(get_project_session)):
    from app.services import managed_launch
    from app.services.actions_v1 import update_permission
    observer=get_observer()
    await observer.ensure_project_schema(project_id)
    async with observer._enable_lock:
        try:
            launch,replay=await managed_launch.prepare(observer,session,project_id=project_id,
                request=payload.model_dump(),initiator=_operator_id(operator))
        except (ValueError,LookupError) as exc:raise HTTPException(409,str(exc)) from exc
        if replay:return managed_launch.launch_json(launch,replay=True)
        try:
            async with observer.master() as master:
                run=await master.get(WorkflowRun,launch.workflow_run_id)
                node=next(n for n in run.nodes_snapshot if n['id']==launch.entry_node)
            result=await start_managed_node(project_id,launch.execution_id,ManagedStart(
                node_key=launch.entry_node,step_code=node['type'],rationale=payload.rationale,
                idempotency_key='launch-start:'+launch.launch_id),operator,session)
            return await managed_launch.finish(observer,launch.launch_id,result=result)
        except BaseException as exc:
            update_permission(project_id,held=True)
            await session.rollback()
            try:await managed_launch.finish(observer,launch.launch_id,error=type(exc).__name__)
            except Exception as audit_error:observer.mark_gap(launch.execution_id,audit_error)
            if isinstance(exc,HTTPException):raise
            if not isinstance(exc,Exception):raise
            raise HTTPException(409,'managed launch held: '+type(exc).__name__) from exc


@router.get('/launch-options')
async def managed_launch_options(project_id:int,operator:AuthDep):
    from app.services.decision_config import status
    from app.services.node_capabilities import capability_for
    from app.services.excel_gpt_node import effective_node_type
    from app.services.managed_launch import ManagedLaunch,launch_json
    async with get_observer().master() as session:
        run=await session.scalar(select(WorkflowRun).where(WorkflowRun.project_id==project_id).order_by(WorkflowRun.id.desc()))
        if not run:raise HTTPException(404,'existing workflow run required')
        states={n.node_key:n for n in (await session.scalars(select(NodeRun).where(NodeRun.workflow_run_id==run.id))).all()}
        latest=await session.scalar(select(ManagedLaunch).where(ManagedLaunch.project_id==project_id).order_by(ManagedLaunch.created_at.desc()))
        nodes=[]
        for node in run.nodes_snapshot or []:
            key=str(node.get('id') or '');capability=capability_for(effective_node_type(node));state=states.get(key)
            data=node.get('data') or {}
            nodes.append({'node_key':key,'title':str(data.get('label') or data.get('title') or key),
                'group_id':data.get('groupId'),'supported':capability.managed,
                'status':state.status.value if state else 'unknown',
                'reason':None if capability.managed else 'исполнитель и проверка ещё не приняты',
                'result_kind':capability.result_kind})
        runtime=status()
        return panel_safe({'workflow_run_id':run.id,'graph_version':digest({'nodes':run.nodes_snapshot,'edges':run.edges_snapshot}),
            'nodes':nodes,'strategies':[a for a in ('wait','human_review','retry_same') if a in runtime['allowed_actions']],
            'request_limit_default':12,'request_limit_max':100,'configured':runtime['configured'],
            'mode':runtime['mode'],'online_authorized':False,
            'reason':runtime['reason'],'latest_launch':launch_json(latest) if latest else None})


class VerifyNode(BaseModel):
    node_key: str = Field(min_length=1, max_length=60)
    rationale: str = Field(min_length=3, max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=120)


class CloseAction(BaseModel):
    rationale: str = Field(min_length=3, max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=120)


class ReviewIncident(BaseModel):
    incident_id: str = Field(min_length=1, max_length=120)
    rationale: str = Field(min_length=3, max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=120)


class RetryNode(ReviewIncident):
    node_key: str = Field(min_length=1, max_length=60)


class DecisionMode(BaseModel):
    enabled: bool
    rationale: str = Field(min_length=3,max_length=1000)


@router.post('/{execution_id}/decisions/control')
async def decision_mode(project_id:int,execution_id:str,payload:DecisionMode,operator:AuthDep):
    from app.services.decision_v1 import get_decider
    observer=get_observer()
    async with observer.master() as s:await _scope(s,project_id,execution_id)
    try:
        return await get_decider(observer).configure(project_id=project_id,execution_id=execution_id,
            enabled=payload.enabled,initiator=_operator_id(operator),rationale=payload.rationale)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


@router.get('/{execution_id}/decisions')
async def decisions(project_id:int,execution_id:str):
    from app.services.decision_state import DecisionControl,DecisionRecord,DecisionTurn
    from app.services.decision_v1 import record_json,MAX_STEPS
    async with get_observer().master() as s:
        await _scope(s,project_id,execution_id)
        control=await s.get(DecisionControl,execution_id)
        rows=(await s.scalars(select(DecisionRecord).where(DecisionRecord.execution_id==execution_id,
            DecisionRecord.project_id==project_id).order_by(DecisionRecord.created_at))).all()
        records=[]
        for row in rows:
            turns=(await s.scalars(select(DecisionTurn).where(DecisionTurn.decision_id==row.decision_id,
                DecisionTurn.role.in_(['assistant','tool'])).order_by(DecisionTurn.id))).all()
            records.append({**record_json(row),'turns':[{'id':t.id,'role':t.role,'payload':t.payload} for t in turns]})
        from app.services.decision_config import status
        runtime=status()
        return panel_safe({'enabled':bool(control and control.enabled),'synthetic_only':runtime['mode']=='synthetic','runtime':runtime,
            'max_model_steps':MAX_STEPS,'records':records})



class ApproveProfile(BaseModel):
    model_config = {"extra":"forbid"}
    node_key: str = Field(min_length=1,max_length=60)
    profile_id: str = Field(min_length=1,max_length=80)
    base_version: str = Field(min_length=64,max_length=64)
    catalog_hash: str = Field(min_length=64,max_length=64)
    rationale: str = Field(min_length=3,max_length=1000)

class CancelProfile(BaseModel):
    model_config = {"extra":"forbid"}
    rationale: str = Field(min_length=3,max_length=1000)

@router.get('/{execution_id}/profiles')
async def profiles(project_id:int,execution_id:str,node_key:str):
    from app.services.profiles_v1 import ProfilesSupervisor
    try:return await ProfilesSupervisor(get_observer()).state(project_id,execution_id,node_key)
    except ValueError as exc:raise HTTPException(404,str(exc)) from exc

@router.post('/{execution_id}/profiles/approve')
async def approve_profile(project_id:int,execution_id:str,payload:ApproveProfile,operator:AuthDep):
    from app.services.profiles_v1 import ProfilesSupervisor
    try:return await ProfilesSupervisor(get_observer()).approve(project_id=project_id,execution_id=execution_id,
        initiator=_operator_id(operator),**payload.model_dump())
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc

@router.post('/{execution_id}/profiles/{overlay_id}/cancel')
async def cancel_profile(project_id:int,execution_id:str,overlay_id:str,payload:CancelProfile,operator:AuthDep):
    from app.services.profiles_v1 import ProfilesSupervisor
    try:return await ProfilesSupervisor(get_observer()).cancel(project_id=project_id,execution_id=execution_id,
        overlay_id=overlay_id,initiator=_operator_id(operator),rationale=payload.rationale)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc


def scope_json(scope):
    return panel_safe({key: getattr(scope, key) for key in (
        "execution_id", "project_id", "workflow_run_id", "node_keys", "graph_version",
        "mode", "observer_state", "created_at", "cursors", "gaps")})


async def _scope(session, project_id, execution_id):
    scope = await session.get(ExecutionControl, execution_id)
    if scope is None or scope.project_id != project_id:
        raise HTTPException(404, "Observation not found for project")
    return scope


@router.post("", status_code=201)
async def enable_observation(project_id: int, payload: EnableObservation):
    observer = get_observer()
    async with observer.master() as session:
        run = (await session.scalars(select(WorkflowRun).where(WorkflowRun.project_id == project_id))).first()
        if run is None:
            raise HTTPException(404, "Existing workflow run required")
        valid = {str(n.get("id")) for n in run.nodes_snapshot or []}
        if not set(payload.node_keys).issubset(valid):
            raise HTTPException(422, "Node scope does not match workflow graph")
        graph_version = digest({"nodes": run.nodes_snapshot, "edges": run.edges_snapshot})
        run_id = run.id
    try:
        scope = await observer.enable(project_id=project_id, workflow_run_id=run_id,
            node_keys=payload.node_keys, graph_version=graph_version, initiator="observation_api")
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return scope_json(scope)


@router.get("")
async def list_observations(project_id: int):
    async with get_observer().master() as session:
        scopes = (await session.scalars(select(ExecutionControl).where(
            ExecutionControl.project_id == project_id).order_by(ExecutionControl.created_at.desc()).limit(100))).all()
        return [scope_json(scope) for scope in scopes]


@router.post("/{execution_id}/stop")
async def stop_observation(project_id: int, execution_id: str):
    observer = get_observer()
    async with observer.master() as session:
        await _scope(session, project_id, execution_id)
    await observer.stop(execution_id, initiator="observation_api")
    return {"observer_state": "stopped", "business_action": None}


def _operator_id(token: str | None) -> str:
    if not token:
        return "local_web_operator"
    return "web_operator:" + hashlib.sha256(token.encode()).hexdigest()[:16]


@router.get("/{execution_id}/actions")
async def action_state(project_id: int, execution_id: str):
    async with get_observer().master() as session:
        await _scope(session, project_id, execution_id)
        control = await session.get(ActionExecutionControl, execution_id)
        records = (await session.scalars(select(ActionRecord).where(
            ActionRecord.execution_id == execution_id,
        ).order_by(ActionRecord.created_at.desc()).limit(100))).all()
        from app.services.actions_recovery import RecoveryAction, ticket_json
        recoveries = (await session.scalars(select(RecoveryAction).where(
            RecoveryAction.execution_id == execution_id))).all()
        return {"configured": control is not None,
            "enabled": bool(control and control.enabled),
            "held": bool(control.held) if control else False,
            "group_id": control.group_id if control else None,
            "node_keys": control.node_keys if control else [],
            "graph_version": control.graph_version if control else None,
            "request_limit": control.request_limit if control else None,
            "requests_used": control.requests_used if control else 0,
            "recoveries": [ticket_json(r) for r in recoveries],
            "records": [{"action_id": r.action_id, "action_kind": r.action_kind,
                "node_key": r.node_key, "attempt_id": r.attempt_id,
                "initiator": r.initiator, "rationale": r.rationale,
                "preconditions": r.preconditions, "status": r.status,
                "verification": r.verification, "created_at": r.created_at}
                for r in records]}


@router.post("/{execution_id}/actions/retry")
async def retry_node(project_id: int, execution_id: str, payload: RetryNode,
                     operator: AuthDep):
    observer = get_observer()
    try:
        return await ActionSupervisor(observer.master).recover_node(project_id=project_id,
            execution_id=execution_id, incident_id=payload.incident_id, node_key=payload.node_key,
            idempotency_key=payload.idempotency_key, initiator=_operator_id(operator), rationale=payload.rationale)
    except (ValueError, LookupError) as exc:
        async with observer.master() as session:
            await _scope(session, project_id, execution_id)
            existing = await session.scalar(select(ActionRecord).where(
                ActionRecord.execution_id == execution_id, ActionRecord.idempotency_key == payload.idempotency_key))
            if not existing:
                session.add(ActionRecord(action_id=str(uuid.uuid4()), execution_id=execution_id,
                    project_id=project_id, action_kind="recovery_denied", node_key=payload.node_key,
                    initiator=_operator_id(operator), rationale=payload.rationale,
                    idempotency_key=payload.idempotency_key, status="rejected",
                    preconditions={"reason": str(exc), "incident_id": payload.incident_id},
                    evidence_ids=[], verification={"dispatched": False}))
                await session.commit()
        raise HTTPException(409, str(exc)) from exc


@router.post("/{execution_id}/actions/enable")
async def enable_actions(project_id: int, execution_id: str, payload: EnableActions,
                         operator: AuthDep):
    observer = get_observer()
    supervisor = ActionSupervisor(observer.master)
    async with observer.master() as session:
        scope = await _scope(session, project_id, execution_id)
        graph_version = scope.graph_version
    try:
        return await supervisor.enable(project_id=project_id, execution_id=execution_id,
            group_id=payload.group_id, node_keys=payload.node_keys,
            graph_version=graph_version, initiator=_operator_id(operator),
            rationale=payload.rationale, request_limit=payload.request_limit)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{execution_id}/actions/control")
async def control_actions(project_id: int, execution_id: str, payload: ActionsControl,
                          operator: AuthDep):
    try:
        return await ActionSupervisor(get_observer().master).set_held(
            project_id=project_id, execution_id=execution_id, held=payload.held,
            initiator=_operator_id(operator), rationale=payload.rationale,
            idempotency_key=payload.idempotency_key)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{execution_id}/actions/close")
async def close_actions(project_id: int, execution_id: str, payload: CloseAction,
                        operator: AuthDep):
    try:
        return await ActionSupervisor(get_observer().master).close(
            project_id=project_id, execution_id=execution_id,
            initiator=_operator_id(operator), rationale=payload.rationale,
            idempotency_key=payload.idempotency_key)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


def managed_step_code(node):
    from app.orchestrator.node_registry import spec_for_node
    spec = spec_for_node(node)
    if spec is None:
        raise ValueError("node type has no native runtime step")
    return spec.step_code


@router.post("/{execution_id}/actions/start")
async def start_managed_node(project_id: int, execution_id: str, payload: ManagedStart,
                             operator: AuthDep,
                             session: AsyncSession = Depends(get_project_session)):
    """Explicitly start one node inside the managed permission using start_step."""
    from sqlalchemy import select
    from app.models import WorkflowRun, NodeRun, NodeRunStatus
    from app.services.actions_v1 import get_permission, evaluate_start
    from app.services.observation import digest

    observer = get_observer()
    initiator = _operator_id(operator)
    permission = get_permission(project_id)
    if permission is None or permission.execution_id != execution_id:
        raise HTTPException(409, "no active managed permission")
    async with observer.master() as master:
        prior = (await master.scalars(select(ActionRecord).where(
            ActionRecord.execution_id == execution_id,
            ActionRecord.idempotency_key == payload.idempotency_key,
        ))).first()
        if prior is not None:
            return {"action_id": prior.action_id, "status": prior.status,
                    "idempotent_replay": True}
        control = await master.get(ActionExecutionControl, execution_id)
        if control is None or not control.enabled or control.held:
            raise HTTPException(409, "managed execution disabled or held")

    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project/workflow run not found")
    async with observer.master() as master:
        control = await master.get(ActionExecutionControl, execution_id)
        run = await master.get(WorkflowRun, control.workflow_run_id) if control else None
    if control is None or run is None or run.project_id != project_id:
        raise HTTPException(404, "managed workflow run not found")
    graph_version = digest({"nodes": run.nodes_snapshot, "edges": run.edges_snapshot})
    decision = evaluate_start(permission, project_id=project_id,
        node_key=payload.node_key, graph_version=graph_version)
    if not decision.allowed:
        raise HTTPException(409, f"actions-v1 denied start: {decision.reason}")
    if bool((project.meta or {}).get("user_stop")):
        raise HTTPException(409, "user_stop is active; actions-v1 never clears it")
    graph_node = next((n for n in run.nodes_snapshot or []
                       if str(n.get("id")) == payload.node_key), None)
    if graph_node is None or str(graph_node.get("type") or "") != payload.step_code:
        raise HTTPException(409, "node type does not match immutable workflow snapshot")
    from app.services.node_capabilities import require_managed_capability
    try:
        require_managed_capability(str(graph_node.get("type") or ""))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    node_run = await session.scalar(select(NodeRun).where(
        NodeRun.workflow_run_id == control.workflow_run_id, NodeRun.node_key == payload.node_key,
    ))
    if node_run is None or node_run.status != NodeRunStatus.pending:
        raise HTTPException(409, "only a pending node can be started; failed nodes require a separately authorized retry")

    action_id = str(uuid.uuid4())
    async with observer.master() as master:
        master.add(ActionRecord(action_id=action_id, execution_id=execution_id,
            project_id=project_id, action_kind="start_node", node_key=payload.node_key,
            initiator=initiator, rationale=payload.rationale.strip(), evidence_ids=[],
            preconditions={"permission_enabled": True, "not_held": True,
                "node_in_scope": True, "graph_version_matches": True,
                "user_stop": False}, idempotency_key=payload.idempotency_key,
            status="dispatching", verification={}))
        await master.commit()
    try:
        await start_step(session, project, managed_step_code(graph_node), node_key=payload.node_key,
                         require_node_fsm=True, explicit_ui_start=False, managed_start=True)
        await session.commit()
        await session.refresh(project)
        await sync_run_for_project(project_id, session=session)
        try:
            await push_runtime_to_master(session, project)
        except Exception:
            pass
        await publish_project_event(project_id, event_type="project_updated",
            payload={"step": payload.step_code, "status": project.status.value,
                     "action_id": action_id})
    except Exception as exc:
        async with observer.master() as master:
            record = await master.get(ActionRecord, action_id)
            if record:
                record.status = "dispatch_failed"
                record.verification = {"error_type": type(exc).__name__}
                await master.commit()
        raise HTTPException(409, f"managed start failed: {type(exc).__name__}") from exc
    async with observer.master() as master:
        record = await master.get(ActionRecord, action_id)
        if record:
            record.status = "submitted"
            record.verification = {"project_status": project.status.value,
                                   "worker_completion": "not_yet_verified"}
            await master.commit()
    return {"action_id": action_id, "status": "submitted", "idempotent_replay": False}


@router.post("/{execution_id}/actions/verify")
async def verify_managed_node(project_id: int, execution_id: str, payload: VerifyNode,
                              operator: AuthDep,
                              session: AsyncSession = Depends(get_project_session)):
    """Record runtime verification without changing business data or node state."""
    from sqlalchemy import select
    if not payload.idempotency_key.strip():
        raise HTTPException(422, "idempotency key required")
    observer = get_observer()
    async with observer.master() as master:
        scope = await _scope(master, project_id, execution_id)
        control = await master.get(ActionExecutionControl, execution_id)
        if control is None or payload.node_key not in control.node_keys:
            raise HTTPException(409, "node is outside the managed permission")
        workflow_run_id = control.workflow_run_id
        workflow = await master.get(WorkflowRun, workflow_run_id)
        graph_matches = bool(workflow and digest({"nodes": workflow.nodes_snapshot,
            "edges": workflow.edges_snapshot}) == control.graph_version == scope.graph_version)
        prior = (await master.scalars(select(ActionRecord).where(
            ActionRecord.execution_id == execution_id,
            ActionRecord.idempotency_key == payload.idempotency_key,
        ))).first()
        if prior is not None:
            return {"action_id": prior.action_id, "status": prior.status,
                    "verification": prior.verification, "idempotent_replay": True}
        rows = (await master.scalars(select(ObservationEvent).where(
            ObservationEvent.execution_id == execution_id,
            ObservationEvent.node_key == payload.node_key,
        ).order_by(ObservationEvent.id.desc()).limit(300))).all()
        latest_start = next((r for r in rows if r.kind == "node_start"), None)
        attempt_id = latest_start.attempt_id if latest_start else None
        attempt_rows = [r for r in rows if attempt_id and r.attempt_id == attempt_id]
        node_ended = any(r.kind == "node_end" for r in attempt_rows)
        evidence = [r.event_id for r in attempt_rows if r.kind in {
            "node_start", "node_end", "node_error", "data_applied", "runtime_check", "node_status"}]
    node_run = await session.scalar(select(NodeRun).where(
        NodeRun.workflow_run_id == workflow_run_id,
        NodeRun.node_key == payload.node_key,
    ).order_by(NodeRun.id.desc()))
    runtime_done = bool(node_run and node_run.status == NodeRunStatus.done)
    evidence_complete = not bool(scope.gaps)
    verified = runtime_done and node_ended and bool(attempt_id) and graph_matches and evidence_complete
    verification = {"node_status_done": runtime_done, "attempt_end_recorded": node_ended,
        "graph_version_matches": graph_matches, "attempt_id": attempt_id,
        "scope_evidence_complete": evidence_complete,
        "semantic_quality_verified": False,
        "observer_state": scope.observer_state}
    action_id = str(uuid.uuid4())
    async with observer.master() as master:
        master.add(ActionRecord(action_id=action_id, execution_id=execution_id,
            project_id=project_id, action_kind="verify_result", node_key=payload.node_key,
            attempt_id=attempt_id, initiator=_operator_id(operator),
            rationale=payload.rationale.strip(), evidence_ids=evidence,
            preconditions={"node_in_managed_scope": True, "graph_version_persisted": True},
            idempotency_key=payload.idempotency_key,
            status="verified" if verified else "human_review", verification=verification))
        await master.commit()
    return {"action_id": action_id, "status": "verified" if verified else "human_review",
            "verification": verification, "idempotent_replay": False}


@router.post("/{execution_id}/actions/review")
async def mark_human_review(project_id: int, execution_id: str, payload: ReviewIncident,
                            operator: AuthDep):
    from sqlalchemy import select
    from app.services.diagnosis import Incident
    observer = get_observer()
    async with observer.master() as master:
        await _scope(master, project_id, execution_id)
        incident = await master.get(Incident, payload.incident_id)
        if incident is None or incident.execution_id != execution_id or incident.project_id != project_id:
            raise HTTPException(404, "incident not found for this execution")
        prior = (await master.scalars(select(ActionRecord).where(
            ActionRecord.execution_id == execution_id,
            ActionRecord.idempotency_key == payload.idempotency_key,
        ))).first()
        if prior is not None:
            return {"action_id": prior.action_id, "status": prior.status, "idempotent_replay": True}
        details = incident.details or {}
        action_id = str(uuid.uuid4())
        master.add(ActionRecord(action_id=action_id, execution_id=execution_id,
            project_id=project_id, action_kind="human_review", node_key=details.get("node_key"),
            attempt_id=details.get("attempt_id"), initiator=_operator_id(operator),
            rationale=payload.rationale.strip(), evidence_ids=list(details.get("event_ids") or []),
            preconditions={"incident_belongs_to_execution": True},
            idempotency_key=payload.idempotency_key, status="human_review",
            verification={"business_action": False, "incident_id": payload.incident_id}))
        await master.commit()
    return {"action_id": action_id, "status": "human_review", "business_action": False,
            "idempotent_replay": False}


@router.get("/{execution_id}/events")
async def history(project_id: int, execution_id: str, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)):
    observer = get_observer()
    async with observer.master() as session:
        await _scope(session, project_id, execution_id)
        rows = (await session.scalars(select(ObservationEvent).where(
            ObservationEvent.execution_id == execution_id, ObservationEvent.id > after,
        ).order_by(ObservationEvent.id).limit(limit + 1))).all()
        return panel_safe({"events": [{key: getattr(row, key) for key in (
            "id", "event_id", "execution_id", "project_id", "workflow_run_id", "node_key",
            "attempt_id", "request_id", "db_scope", "kind", "payload", "created_at",
        )} for row in rows[:limit]], "has_more": len(rows) > limit,
            "next_cursor": rows[min(len(rows), limit)-1].id if rows else after})


@router.get("/{execution_id}/context")
async def context(project_id: int, execution_id: str):
    observer = get_observer()
    async with observer.master() as session:
        scope = await _scope(session, project_id, execution_id)
        master, project = await _business_snapshot(session, scope)
        rows = (await session.scalars(select(ObservationEvent).where(
            ObservationEvent.execution_id == execution_id).order_by(ObservationEvent.id.desc()).limit(500))).all()
        result = scope_json(scope)
        from app.services.media_view import context_for_nodes
        media = await context_for_nodes(session, scope)
    gaps = list(result["gaps"] or [])
    replica = None
    if project and (Path(project.data_dir) / "project.db").exists():
        try:
            from app.project_db import get_project_sessionmaker
            factory = await get_project_sessionmaker(project.data_dir)
            async with factory() as session:
                replica, _ = await _business_snapshot(session, scope)
                async with observer.master() as master_session:
                    media = await context_for_nodes(master_session, scope, project_session=session)
        except Exception as exc:
            gaps.append({"kind": "project_snapshot_unavailable", "error_type": type(exc).__name__})
    else:
        gaps.append({"kind": "project_database_not_available"})
    states = (replica or master)["nodes"]
    statuses = [v["status"] for v in states.values()]
    outcome = "done" if len(states) == len(scope.node_keys) and all(s == "done" for s in statuses) else (
        "failed" if "failed" in statuses else "incomplete")
    end_attempts = {row.attempt_id for row in rows if row.kind in {"node_end", "node_error"}}
    incomplete = [{"node_key": row.node_key, "attempt_id": row.attempt_id} for row in rows
                  if row.kind == "node_start" and row.attempt_id not in end_attempts]
    if incomplete:
        gaps.append({"kind": "attempts_without_recorded_end", "attempts": incomplete})
    ended_requests = {row.request_id for row in rows if row.kind in {"model_end", "model_error"}}
    stream_completed = {row.request_id for row in rows if row.kind == "model_stream_event" and row.payload.get("complete")}
    pending_requests = [{"request_id": row.request_id, "node_key": row.node_key,
                         "transport_completed": row.request_id in stream_completed,
                         "completion_unknown": row.request_id not in stream_completed}
                        for row in rows if row.kind == "model_start" and row.request_id not in ended_requests]
    if pending_requests:
        gaps.append({"kind": "requests_without_recorded_end", "requests": pending_requests})
    for row in rows:
        reference = (row.payload or {}).get("body")
        if isinstance(reference, dict) and reference.get("gap"):
            gaps.append({"kind": "body_not_saved", "event_id": row.event_id, "reason": reference["gap"]})
        elif isinstance(reference, dict) and reference.get("path"):
            target = (observer.body_root / reference["path"]).resolve()
            if not target.is_relative_to(observer.body_root.resolve()) or not target.is_file():
                gaps.append({"kind": "body_not_available", "event_id": row.event_id})
    if execution_id in observer.failures:
        gaps.append({"kind": "observer_error", "error_type": observer.failures[execution_id]})
    node_contexts = {}
    for key in scope.node_keys:
        node_rows = [row for row in rows if row.node_key == key]
        start = next((row for row in node_rows if row.kind == "node_start"), None)
        model = next((row for row in node_rows if row.kind == "model_start"), None)
        node_contexts[key] = {
            **({"media":media[key]} if key in media else {}),
            "latest_attempt_id": start.attempt_id if start else None,
            "model": model.payload.get("model") if model else None,
            "settings_revision": model.payload.get("settings_revision") if model else None,
            "input_refs": [{"event_id": row.event_id, "request_id": row.request_id,
                            "body": row.payload.get("body")} for row in node_rows
                           if row.kind in {"model_start", "node_input", "apply_input"}],
            "output_refs": [{"event_id": row.event_id, "request_id": row.request_id,
                             "body": row.payload.get("body")} for row in node_rows
                            if row.kind in {"model_end", "node_output", "artifact_created"}],
            "checks": [{"kind": row.kind, "request_id": row.request_id, "result": row.payload}
                       for row in node_rows if row.kind in {"parse_result", "domain_check", "runtime_check"}],
            "retry_recorded": any(row.kind == "json_retry" for row in node_rows),
        }
    problem = next((row for row in rows if row.kind in {"node_error", "model_error", "apply_error"}
                    or (row.kind == "parse_result" and row.payload.get("payload_required")
                        and not row.payload.get("payload_present"))), None)
    result.update({"master": master, "project": replica, "node_contexts": node_contexts,
        "wait_reasons": {key: next((row.payload for row in rows if row.kind == "runtime_wait" and row.node_key == key
                                   and row.id > max((r.id for r in rows if r.kind == "node_start" and r.node_key == key), default=0)),
            {"reason": "waiting_hitl", "initiator": None}) for key, state in states.items()
            if state["status"] == "waiting_hitl" or (state["status"] not in {"done", "skipped"} and any(row.kind == "runtime_wait" and row.node_key == key
                and row.id > max((r.id for r in rows if r.kind == "node_start" and r.node_key == key), default=0) for row in rows))},
        "pinned": observer.is_pinned(execution_id),
        "commit_confirmation": observer.commit_uncertainty.get(execution_id),
        "disagreement": replica is not None and master != replica,
        "business_group_outcome": outcome,
        "current_nodes": [key for key, state in states.items() if state["status"] in {"running", "queued", "waiting_hitl"}],
        "latest_problem": {"kind": problem.kind, "node_key": problem.node_key,
            "event_id": problem.event_id, "request_id": problem.request_id, "payload": problem.payload,
            "later_apply_committed": any(row.node_key == problem.node_key and row.id > problem.id
                                        and row.kind == "data_applied" for row in rows)} if problem else None,
        "data_gaps": gaps,
        "unknown": ["Semantic diagnosis is not implemented", "Scope does not stop the pipeline",
                    "Formal node schema validation is not_checked unless an actual check event is recorded",
                    "Wait reasons and human identity remain unknown when no runtime fact is recorded"],
        "history_window_limited": len(rows) == 500,
    })
    return panel_safe(result)


@router.post("/{execution_id}/pin")
async def pin_observation(project_id: int, execution_id: str):
    observer = get_observer()
    async with observer.master() as session:
        await _scope(session, project_id, execution_id)
    observer.pin(execution_id)
    return {"pinned": True}


@router.get("/{execution_id}/diagnoses")
async def diagnoses(project_id: int, execution_id: str):
    """Read-only persisted projection; no provider, worker or diagnostic writes."""
    from datetime import datetime
    from sqlalchemy import func
    from app.services.diagnosis import Incident, DiagnosisState, get_diagnoser
    observer=get_observer()
    service=get_diagnoser(observer)
    async with observer.master() as session:
        await _scope(session,project_id,execution_id)
        state=await session.get(DiagnosisState,execution_id)
        head=(await session.scalar(select(func.max(ObservationEvent.id)).where(
            ObservationEvent.execution_id==execution_id,ObservationEvent.project_id==project_id))) or 0
        incidents=(await session.scalars(select(Incident).where(Incident.execution_id==execution_id,
            Incident.project_id==project_id).order_by(Incident.detected_at,Incident.incident_id))).all()
        error=service.failures.get(execution_id) or service.failures.get('consumer')
        available=state is not None and error is None
        lagging=state is None or state.cursor<head or (datetime.utcnow()-state.checked_at).total_seconds()>8
        items=[{**row.details,'incident_id':row.incident_id,'status':row.status,
                'detected_at':row.detected_at,'updated_at':row.updated_at} for row in incidents]
        return panel_safe({'available':available,'lagging':lagging,'error_type':error,
            'cursor':state.cursor if state else 0,'journal_head':head,'checked_at':state.checked_at if state else None,
            'rule_version':state.rule_version if state else None,'incidents':items,
            'evidence_complete':available and not lagging and not any(i.get('evidence_incomplete') for i in items if i.get('currently_relevant')),
            'blocking_count':sum(bool(i.get('blocking') and i.get('currently_relevant') and i['status']!='resolved_confirmed') for i in items),
            'automatic_actions':False,'semantic_quality_verified':False})
