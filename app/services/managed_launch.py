"""Durable, opt-in entry to the existing observer/worker runtime.

This coordinates permission records, not node execution. A dispatch interrupted
between the control DB and project DB is held, never automatically reissued.
"""
from datetime import datetime
import uuid

from sqlalchemy import JSON, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, NodeRun, NodeRunStatus, Project, ProjectStatus, WorkflowRun
from app.services.observation import ExecutionControl, digest


class ManagedLaunch(Base):
    __tablename__ = 'managed_launches'
    __table_args__ = (
        UniqueConstraint('project_id','idempotency_key',name='uq_managed_launch_key'),
        UniqueConstraint('project_id','workflow_run_id',name='uq_managed_launch_run'),
    )
    launch_id: Mapped[str] = mapped_column(String(36),primary_key=True)
    execution_id: Mapped[str] = mapped_column(String(36),unique=True)
    project_id: Mapped[int] = mapped_column(index=True)
    workflow_run_id: Mapped[int] = mapped_column(index=True)
    idempotency_key: Mapped[str] = mapped_column(String(120))
    request_hash: Mapped[str] = mapped_column(String(64))
    policy: Mapped[dict] = mapped_column(JSON)
    entry_node: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(24),default='dispatching')
    result: Mapped[dict] = mapped_column(JSON,default=dict)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


def launch_json(row, *, replay=False):
    return {k:getattr(row,k) for k in ('launch_id','execution_id','workflow_run_id',
        'entry_node','status','result','policy')} | {'idempotent_replay':replay}


def require_idle_project(project):
    from app.services.step_cancel import is_generation_active,stop_flag_path
    from app.services.step_failure_policy import is_sleeping
    from app.telegram.menu import is_running_status
    if project is None:raise ValueError('project unavailable')
    meta=project.meta or {}
    if meta.get('user_stop') or meta.get('mass_lane_user_stop') or stop_flag_path(project.id).exists():
        raise ValueError('user_stop is active; managed launch cannot clear it')
    if is_sleeping(project) or (meta.get('step_failure') or {}).get('abandoned_at'):
        raise ValueError('native backoff/exhaustion requires the existing recovery flow')
    if is_generation_active(project.id) or is_running_status(project.status):
        raise ValueError('project already has active work')


def validate_scope(run, project, rows, selected):
    from app.orchestrator.graph.planner import WorkflowGraph
    from app.services.excel_gpt_node import effective_node_type
    from app.services.node_capabilities import require_managed_capability
    if not selected or len(selected)!=len(set(selected)):raise ValueError('explicit unique node scope required')
    graph=WorkflowGraph(run.nodes_snapshot,run.edges_snapshot)
    states={n.node_key:n for n in rows}
    contracts={}
    if any(n.status in {NodeRunStatus.running,NodeRunStatus.queued,NodeRunStatus.waiting_hitl} for n in rows):
        raise ValueError('active node or HITL must settle first')
    for key in selected:
        node=graph._by_id.get(key)
        if not node or key not in states:raise ValueError('node outside immutable workflow snapshot')
        if states[key].status!=NodeRunStatus.pending or states[key].attempts:
            raise ValueError('launch requires fresh pending nodes; retry uses recovery')
        contracts[key]=require_managed_capability(effective_node_type(node)).contract()
    skipped=graph.skipped_keys(project)
    if set(selected)&skipped:raise ValueError('selected node is disabled')
    roots=[]
    for key in selected:
        predecessors=graph._effective_predecessors(key,skipped)
        if not predecessors.intersection(selected):
            if any(p not in states or states[p].status!=NodeRunStatus.done for p in predecessors):
                raise ValueError('entry prerequisites are not done')
            if any(graph.gate_blocks_edge(project,p,key) for p in graph._incoming_prereq_sources(key)):
                raise ValueError('entry domain gate is not satisfied')
            roots.append(key)
    if len(roots)!=1:raise ValueError('select one connected subgraph with an unambiguous entry')
    reachable=set();todo=[roots[0]]
    while todo:
        key=todo.pop()
        if key in reachable:continue
        reachable.add(key)
        todo.extend(k for k in graph._pipeline_successors(key) if k in selected)
    if reachable!=set(selected):raise ValueError('scope contains disconnected nodes')
    return roots[0],contracts


async def prepare(observer, project_session, *, project_id, request, initiator):
    """Caller holds observer._enable_lock through dispatch, including API replays."""
    from app.services.actions_v1 import ActionExecutionControl,ActionRecord,ExecutionPermission,install_permission
    from app.services.decision_state import DecisionControl
    from app.services.decision_config import runtime_config,check_route,DecisionRuntimeBinding
    identity=digest(request)
    async with observer.master() as s:
        prior=await s.scalar(select(ManagedLaunch).where(ManagedLaunch.project_id==project_id,
            ManagedLaunch.idempotency_key==request['idempotency_key']))
        if prior:
            if prior.request_hash!=identity:raise ValueError('idempotency payload conflict')
            return prior,True
        # A different execution/key cannot restart this run's allowance.
        prior_run=await s.scalar(select(ManagedLaunch.launch_id).where(ManagedLaunch.project_id==project_id,
            ManagedLaunch.workflow_run_id==request['workflow_run_id']))
        if prior_run:raise ValueError('workflow already has a managed launch; use its persisted execution')
        active=await s.scalar(select(ExecutionControl.execution_id).where(ExecutionControl.project_id==project_id,
            ExecutionControl.observer_state.in_(['active','degraded'])))
        prior_control=await s.scalar(select(ActionExecutionControl.execution_id).where(
            ActionExecutionControl.project_id==project_id,
            (ActionExecutionControl.workflow_run_id==request['workflow_run_id']) | ActionExecutionControl.enabled.is_(True)))
        if active or prior_control:raise ValueError('existing observation/managed history must be handled explicitly')
        run=await s.get(WorkflowRun,request['workflow_run_id'])
        project=await s.get(Project,project_id)
        primary=await project_session.get(Project,project_id)
        require_idle_project(project);require_idle_project(primary)
        if not run or run.project_id!=project_id:raise ValueError('workflow/project mismatch')
        revision=digest({'nodes':run.nodes_snapshot,'edges':run.edges_snapshot})
        if revision!=request['graph_version']:raise ValueError('graph revision changed')
        from app.services.canvas_graph import canvas_graph_from_meta
        canvas=canvas_graph_from_meta(primary.meta)
        if canvas and digest({k:canvas[k] for k in ('nodes','edges')})!=revision:
            raise ValueError('runtime canvas and selected graph disagree')
        rows=(await s.scalars(select(NodeRun).where(NodeRun.workflow_run_id==run.id))).all()
        entry,contracts=validate_scope(run,project,rows,request['node_keys'])
        replica=await project_session.get(WorkflowRun,run.id)
        replica_rows=(await project_session.scalars(select(NodeRun).where(NodeRun.workflow_run_id==run.id))).all()
        if (not replica or digest({'nodes':replica.nodes_snapshot,'edges':replica.edges_snapshot})!=revision
                or {r.node_key:(r.status,r.attempts) for r in rows}!={r.node_key:(r.status,r.attempts) for r in replica_rows}):
            raise ValueError('master/project state disagreement')
        initial_status_projection=None
        if project.status!=primary.status:
            # GET can derive an idle ready status in the authoritative project DB
            # before its master projection catches up. Only the untouched `new`
            # projection may be refreshed; active work/stops were rejected above,
            # and graph, every NodeRun and attempt must still agree exactly.
            if project.status!=ProjectStatus.new:raise ValueError('master/project status disagreement requires review')
            initial_status_projection={'from':project.status.value,'to':primary.status.value,'source':'project_db',
                'reason':'idle project readiness computed by original runtime'}
        cfg=runtime_config();check_route(cfg,cfg['endpoint'])
        strategies=request['strategies']
        if not strategies or len(strategies)!=len(set(strategies)) or not set(strategies)<= {'wait','human_review','retry_same'}:
            raise ValueError('unsupported strategy catalog')
        if not set(strategies)<=set(cfg['allowed_actions']):raise ValueError('strategy outside configured owner policy')
        from app.services.decision_config import owner_budget_policy
        from app.services.actions_v1 import claim_run_allowance
        owner_policy=owner_budget_policy(cfg)
        if type(request['request_limit'])!=int or not 1<=request['request_limit']<=owner_policy['max_requests']:
            raise ValueError('request budget outside configured owner policy')
        execution=str(uuid.uuid4());launch_id=str(uuid.uuid4())
        policy={'graph_version':revision,'node_keys':list(request['node_keys']),
            'allowed_actions':sorted(set(strategies)|({'read_events','read_contract','finish'}&set(cfg['allowed_actions']))),
            'request_limit':request['request_limit'],'max_decision_steps':3,'node_recovery_limit':1,
            'contracts':contracts,'runtime_config_hash':digest(cfg),'owner_budget_policy':owner_policy}
        await claim_run_allowance(s,project_id=project_id,workflow_run_id=run.id,
            execution_id=execution,request_limit=request['request_limit'],owner_policy=owner_policy)
        if initial_status_projection:
            project.status=primary.status
            policy['initial_status_projection']=initial_status_projection
        row=ManagedLaunch(launch_id=launch_id,execution_id=execution,project_id=project_id,workflow_run_id=run.id,
            idempotency_key=request['idempotency_key'],request_hash=identity,policy=policy,entry_node=entry,
            status='dispatching',result={})
        scope=ExecutionControl(execution_id=execution,project_id=project_id,workflow_run_id=run.id,
            node_keys=request['node_keys'],graph_version=revision,mode='managed',observer_state='active',cursors={},gaps=[])
        control=ActionExecutionControl(execution_id=execution,project_id=project_id,workflow_run_id=run.id,
            group_id='scope:'+launch_id,node_keys=request['node_keys'],graph_version=revision,
            enabled=True,held=False,request_limit=request['request_limit'],requests_used=0,
            initiator=initiator,rationale=request['rationale'])
        s.add_all([row,scope,control,
            DecisionControl(execution_id=execution,project_id=project_id,enabled=True,initiator=initiator,rationale=request['rationale']),
            DecisionRuntimeBinding(execution_id=execution,config_hash=digest(cfg),config=cfg),
            ActionRecord(action_id=str(uuid.uuid4()),execution_id=execution,project_id=project_id,
                action_kind='managed_launch',initiator=initiator,rationale=request['rationale'],
                evidence_ids=[],preconditions=policy,idempotency_key='launch:'+launch_id,status='accepted',verification={})])
        observer.stage(s,scope,'observer_control',db_scope='master',payload={
            'action':'managed_launch','initiator':initiator,'launch_id':launch_id,'entry_node':entry})
        await s.commit()
    observer.scopes[run.id]=scope
    install_permission(ExecutionPermission(enabled=True,held=False,project_id=project_id,
        execution_id=execution,group_id=control.group_id,graph_version=revision,node_keys=tuple(request['node_keys']),
        request_limit=request['request_limit'],requests_used=0))
    return row,False


async def finish(observer, launch_id, *, result=None, error=None):
    from app.services.actions_v1 import ActionExecutionControl,update_permission
    from app.services.decision_state import DecisionControl
    async with observer.master() as s:
        row=await s.get(ManagedLaunch,launch_id)
        if error is not None:
            update_permission(row.project_id,held=True)
            control=await s.get(ActionExecutionControl,row.execution_id);control.held=True
            decision=await s.get(DecisionControl,row.execution_id);decision.enabled=False
            row.status='held';row.result={'reason':error,'new_dispatch_allowed':False}
        else:row.status='submitted';row.result=result
        await s.commit()
        return launch_json(row)


async def hold_interrupted_launches(master):
    from app.services.actions_v1 import ActionExecutionControl
    from app.services.decision_state import DecisionControl
    async with master() as s:
        rows=(await s.scalars(select(ManagedLaunch).where(ManagedLaunch.status=='dispatching'))).all()
        for row in rows:
            control=await s.get(ActionExecutionControl,row.execution_id)
            if control:control.held=True
            decision=await s.get(DecisionControl,row.execution_id)
            if decision:decision.enabled=False
            row.status='held';row.result={'reason':'interrupted launch; dispatch outcome requires human review','new_dispatch_allowed':False}
        if rows:await s.commit()


async def allowed_actions(session, execution_id, configured):
    row=await session.scalar(select(ManagedLaunch).where(ManagedLaunch.execution_id==execution_id))
    return set(configured) if row is None else set(configured)&set(row.policy['allowed_actions'])


async def native_entry(session,project,step_code,node_key):
    """Initial launch only: original queue and FSM, no manual reset/backoff clearing."""
    from app.services.actions_v1 import get_permission,evaluate_start
    from app.services.gen_queue import assert_can_start_in_queue
    from app.services.node_capabilities import require_managed_capability
    from app.services.excel_gpt_node import effective_node_type,ensure_enrich_auto_chain_to,slot_index_from_node
    from app.orchestrator.node_registry import spec_for_node
    from app.services.run_sync import prepare_node_for_step_start
    require_idle_project(project)
    run=await session.scalar(select(WorkflowRun).where(WorkflowRun.project_id==project.id))
    node=next((n for n in run.nodes_snapshot if n.get('id')==node_key),None) if run else None
    if not node:raise ValueError('managed entry node not found')
    require_managed_capability(effective_node_type(node))
    permission=get_permission(project.id)
    if not permission or not evaluate_start(permission,project_id=project.id,node_key=node_key,
            graph_version=digest({'nodes':run.nodes_snapshot,'edges':run.edges_snapshot})).allowed:
        raise ValueError('managed start denied')
    spec=spec_for_node(node)
    if not spec or spec.step_code!=step_code:raise ValueError('native step contract mismatch')
    row=await session.scalar(select(NodeRun).where(NodeRun.workflow_run_id==run.id,NodeRun.node_key==node_key))
    if not row or row.status!=NodeRunStatus.pending or row.attempts:raise ValueError('initial pending attempt required')
    await assert_can_start_in_queue(session,project)
    if effective_node_type(node)=='excel_gpt':
        project.meta={**(project.meta or {}),'active_excel_gpt_node_key':node_key}
        if slot_index_from_node(node)>0:ensure_enrich_auto_chain_to(project,slot_index_from_node(node),from_key=node_key)
    await prepare_node_for_step_start(session,project,step_code,node_key=node_key,strict=True,explicit_ui_start=False)
    project.status=spec.running_status;project.updated_at=datetime.utcnow()
    await session.flush()
    return project.status
