"""Safety contracts for the actions-v1 execution permission layer."""
import unittest
import tempfile
import uuid
import asyncio
from unittest.mock import AsyncMock, patch
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.services import revisions
from app.services.actions_v1 import (
    ActionSupervisor,
    ActionExecutionControl,
    ExecutionPermission,
    action_is_idempotent,
    clear_permission,
    evaluate_start,
    install_permission,
    get_permission,
    reload_permissions,
)
from app.models import Base, NodeRun, NodeRunStatus, Project, Workflow, WorkflowRun
from app.services.observation import ExecutionControl, digest
from app.services.actions_recovery import RecoveryAction
from app.services.node_status_machine import start_node_running
from app.services import observation
from app.services.diagnosis import Incident, DiagnosisState
from app.services.actions_recovery import claim, input_revision
from app.services.managed_launch import ManagedLaunch
from app.services.profiles_state import ProfileOverlay
from sqlalchemy import select


class ActionsContract(unittest.TestCase):
    def tearDown(self):
        clear_permission(7)

    def permission(self, **overrides):
        values = dict(enabled=False, held=False, project_id=7, execution_id="exec-1",
                      group_id="group-a", graph_version="graph-v1", node_keys=("n1", "n2"),
                      request_limit=12, requests_used=0)
        values.update(overrides)
        return ExecutionPermission(**values)

    def test_default_off_blocks_start(self):
        self.assertFalse(evaluate_start(self.permission(), project_id=7, node_key="n1",
                                        graph_version="graph-v1").allowed)

    def test_explicit_permission_is_limited_to_group_and_graph(self):
        p = self.permission(enabled=True)
        self.assertTrue(evaluate_start(p, project_id=7, node_key="n2", graph_version="graph-v1").allowed)
        self.assertFalse(evaluate_start(p, project_id=7, node_key="n3", graph_version="graph-v1").allowed)
        self.assertFalse(evaluate_start(p, project_id=7, node_key="n1", graph_version="graph-v2").allowed)
        self.assertFalse(evaluate_start(p, project_id=8, node_key="n1", graph_version="graph-v1").allowed)

    def test_hold_and_budget_block_without_mutating_permission(self):
        held = evaluate_start(self.permission(enabled=True, held=True), project_id=7,
                              node_key="n1", graph_version="graph-v1")
        spent = evaluate_start(self.permission(enabled=True, requests_used=12), project_id=7,
                               node_key="n1", graph_version="graph-v1")
        self.assertEqual(held.reason, "execution_held")
        self.assertEqual(spent.reason, "request_budget_exhausted")

    def test_action_idempotency_is_execution_scoped(self):
        self.assertTrue(action_is_idempotent("exec-1", "action-1", "exec-1", "action-1"))
        self.assertFalse(action_is_idempotent("exec-1", "action-1", "exec-1", "action-2"))
        self.assertFalse(action_is_idempotent("exec-1", "action-1", "exec-2", "action-1"))

    def test_fsm_blocks_nodes_outside_the_explicit_group(self):
        install_permission(self.permission(enabled=True))
        node = NodeRun(workflow_run_id=1, node_key="n3", node_type="script", status=NodeRunStatus.pending)
        self.assertFalse(start_node_running(node, project_id=7, initiator="worker"))
        self.assertEqual(node.status, NodeRunStatus.pending)

    def test_fsm_runs_allowed_nodes_and_leaves_unmanaged_projects_unchanged(self):
        install_permission(self.permission(enabled=True))
        node = NodeRun(workflow_run_id=1, node_key="n2", node_type="script", status=NodeRunStatus.pending)
        self.assertTrue(start_node_running(node, project_id=7, initiator="worker"))
        self.assertEqual(node.status, NodeRunStatus.running)
        other = NodeRun(workflow_run_id=2, node_key="n3", node_type="script", status=NodeRunStatus.pending)
        self.assertTrue(start_node_running(other, project_id=8, initiator="worker"))
        self.assertEqual(other.status, NodeRunStatus.running)


class DurableActions(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.tmp.name) / 'master.db'}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await observation.migrate_observation_schema(conn)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        # Recovery evidence below is the structured Excel/mandatory JSON contract.
        fixture_type = getattr(self, "fixture_node_type", "excel_gpt")
        self.nodes = [{"id":"n1","type":fixture_type,"data":{"groupId":"g1"}},
                      {"id":"n2","type":"script","data":{"groupId":"g2"}}]
        self.graph = revisions.graph_version(self.nodes, [])
        async with self.sessions() as session:
            session.add(Project(id=7,slug="actions-fixture",topic="synthetic",meta={}))
            session.add(Workflow(id=9,name="actions fixture",nodes=self.nodes,edges=[]))
            session.add(WorkflowRun(id=11,workflow_id=9,project_id=7,nodes_snapshot=self.nodes,edges_snapshot=[]))
            session.add_all([
                NodeRun(workflow_run_id=11,node_key="n1",node_type=fixture_type,status=NodeRunStatus.pending),
                NodeRun(workflow_run_id=11,node_key="n2",node_type="script",status=NodeRunStatus.pending),
                ExecutionControl(execution_id="exec-durable",project_id=7,workflow_run_id=11,
                    node_keys=["n1","n2"],graph_version=self.graph,mode="observe",
                    observer_state="active",created_at=datetime.utcnow(),cursors={},gaps=[]),
            ])
            await session.commit()
        self.supervisor = ActionSupervisor(self.sessions)
        self.saved_observer = observation._observer
        observation._observer = observation.Observer(self.sessions, body_root=Path(self.tmp.name) / 'bodies')

    async def asyncTearDown(self):
        clear_permission(7)
        from app.services.actions_recovery import finish_job_tracking
        finish_job_tracking(7)
        observation._observer = self.saved_observer
        await self.engine.dispose()
        self.tmp.cleanup()

    async def test_opt_in_budget_hold_restart_and_user_stop_are_safe(self):
        enabled = await self.supervisor.enable(project_id=7,execution_id="exec-durable",group_id="g1",
            node_keys=["n1"],graph_version=self.graph,initiator="test-operator",
            rationale="synthetic acceptance",request_limit=1)
        self.assertTrue(enabled["enabled"])
        async with self.sessions() as session:
            scope=await session.get(ExecutionControl,"exec-durable")
            self.assertEqual(scope.mode,"managed")
            self.assertTrue(await session.get(ActionExecutionControl,"exec-durable"))
        self.assertEqual(evaluate_start(get_permission(7),project_id=7,node_key="n2",
            graph_version=self.graph).reason,"outside_execution_scope")
        self.assertEqual(await self.supervisor.charge_request("exec-durable"),1)
        with self.assertRaisesRegex(RuntimeError,"budget exhausted"):
            await self.supervisor.charge_request("exec-durable")
        await self.supervisor.set_held(project_id=7,execution_id="exec-durable",held=True,
            initiator="test-operator",rationale="pause test",idempotency_key="pause-key-0001")
        await reload_permissions(self.sessions)
        self.assertTrue(get_permission(7).held)
        async with self.sessions() as session:
            project=await session.get(Project,7)
            project.meta={"user_stop":True}
            await session.commit()
        with self.assertRaisesRegex(ValueError,"user_stop"):
            await self.supervisor.set_held(project_id=7,execution_id="exec-durable",held=False,
                initiator="test-operator",rationale="resume test",idempotency_key="resume-key-0001")

    async def test_held_execution_blocks_original_job_before_business_session_even_after_restart(self):
        from app.services.advance_runner import advance_project_job
        await self.supervisor.enable(project_id=7,execution_id='exec-durable',group_id='g1',
            node_keys=['n1'],graph_version=self.graph,initiator='test-operator',
            rationale='worker admission acceptance',request_limit=8)
        await self.supervisor.set_held(project_id=7,execution_id='exec-durable',held=True,
            initiator='test-operator',rationale='held native verifier',idempotency_key='held-worker-test')
        for restarted in (False,True):
            if restarted:clear_permission(7)
            with patch('app.services.advance_runner.project_db_session_scope',
                       side_effect=AssertionError('held worker must not open business session')):
                result=await advance_project_job(7,None)
            self.assertIsNone(result.new_status)
        async with self.sessions() as s:
            control=await s.get(ActionExecutionControl,'exec-durable')
            self.assertTrue(control.held)
            self.assertEqual(control.requests_used,0)

    async def test_worker_admission_preserves_other_projects_and_observation_stop(self):
        from app.services.actions_v1 import worker_entry_admission
        self.assertTrue((await worker_entry_admission(8)).allowed)
        await self.supervisor.enable(project_id=7,execution_id='exec-durable',group_id='g1',
            node_keys=['n1'],graph_version=self.graph,initiator='test-operator',
            rationale='independent observation stop',request_limit=8)
        async with self.sessions() as s:
            (await s.get(ExecutionControl,'exec-durable')).observer_state='stopped'
            await s.commit()
        self.assertTrue((await worker_entry_admission(7)).allowed)
        await self.supervisor.set_held(project_id=7,execution_id='exec-durable',held=True,
            initiator='test-operator',rationale='hold only this execution',idempotency_key='hold-scope-test')
        self.assertFalse((await worker_entry_admission(7)).allowed)
        self.assertTrue((await worker_entry_admission(8)).allowed)

    async def test_verifier_hold_is_not_a_user_action_and_cannot_automatically_resume(self):
        from sqlalchemy import select
        from app.services.actions_v1 import ActionRecord
        await self.supervisor.enable(project_id=7,execution_id='exec-durable',group_id='g1',
            node_keys=['n1'],graph_version=self.graph,initiator='test-operator',
            rationale='verifier provenance',request_limit=8)
        await self.supervisor.set_held(project_id=7,execution_id='exec-durable',held=True,
            initiator='native_split_verifier',rationale='coverage failed',idempotency_key='verifier-hold-test',
            operator_request=False)
        async with self.sessions() as s:
            row=await s.scalar(select(ActionRecord).where(ActionRecord.idempotency_key=='verifier-hold-test'))
            self.assertFalse(row.preconditions['explicit_operator_request'])
        with self.assertRaisesRegex(ValueError,'automatic resume'):
            await self.supervisor.set_held(project_id=7,execution_id='exec-durable',held=False,
                initiator='native_split_verifier',rationale='not permitted',idempotency_key='verifier-resume-test',
                operator_request=False)

    async def test_recovery_requires_actual_current_incident_before_any_dispatch(self):
        await self.supervisor.enable(project_id=7,execution_id="exec-durable",group_id="g1",
            node_keys=["n1"],graph_version=self.graph,initiator="test-operator",
            rationale="synthetic acceptance",request_limit=20)
        with self.assertRaisesRegex(ValueError,"incident"):
            await self.supervisor.recover_node(project_id=7,execution_id="exec-durable",
                incident_id="absent",node_key="n1",idempotency_key="recovery-test-0001",
                initiator="test-operator",rationale="synthetic recovery")
        async with self.sessions() as session:
            node=await session.scalar(__import__('sqlalchemy').select(NodeRun).where(NodeRun.node_key=='n1'))
            self.assertEqual(node.status,NodeRunStatus.pending)

    async def test_parallel_http_reservations_never_exceed_budget(self):
        await self.supervisor.enable(project_id=7,execution_id='exec-durable',group_id='g1',
            node_keys=['n1'],graph_version=self.graph,initiator='test',rationale='parallel budget',request_limit=1)
        results=await asyncio.gather(*(self.supervisor.charge_request('exec-durable') for _ in range(6)),
            return_exceptions=True)
        self.assertEqual(sum(isinstance(r,int) for r in results),1)
        await reload_permissions(self.sessions)
        self.assertEqual(get_permission(7).requests_used,1)

    async def exhausted_fixture(self, *, node_keys=None):
        from app.models import ProjectStatus
        await self.supervisor.enable(project_id=7,execution_id='exec-durable',group_id='g1',
            node_keys=node_keys or ['n1'],graph_version=self.graph,initiator='test',rationale='synthetic',request_limit=20)
        async with self.sessions() as s:
            project = await s.get(Project,7)
            project.status=ProjectStatus.paused
            project.meta={'active_excel_gpt_node_key':'n1','step_failure':{
                'total_fails':{'enriching_1':9},'last_running':'enriching_1','abandoned_at':'synthetic'}}
            node=await s.scalar(select(NodeRun).where(NodeRun.node_key=='n1'))
            node.status=NodeRunStatus.failed;node.finished_at=datetime.utcnow();node.attempts=9
            revision=await input_revision(s,project,'n1')
            facts=[('node_start',{'recovery_input_revision':revision},None),
                ('model_start',{'input_revision':digest({'test':1}),'provider':'kie'},'request-source'),
                ('model_end',{'complete':True},'request-source'),('json_retry',{},None),
                ('parse_result',{'payload_required':True,'payload_present':False},'request-source'),
                ('node_error',{},None)]
            for idx,(kind,payload,request_id) in enumerate(facts,1):
                s.add(observation.ObservationEvent(id=idx,event_id=str(uuid.uuid4()),execution_id='exec-durable',
                    project_id=7,workflow_run_id=11,node_key='n1',attempt_id='source-attempt',request_id=request_id,
                    db_scope='master',kind=kind,payload=payload,created_at=datetime.utcnow()))
            s.add(Incident(incident_id='source-incident',execution_id='exec-durable',project_id=7,node_key='n1',
                attempt_id='source-attempt',type='missing_payload',severity='error',status='human_review',
                details={'currently_relevant':True,'event_ids':[]}))
            s.add(DiagnosisState(execution_id='exec-durable',cursor=6))
            await s.commit()

    async def retry(self, **overrides):
        kwargs=dict(project_id=7,execution_id='exec-durable',incident_id='source-incident',node_key='n1',
                    idempotency_key='safe-retry-0001',initiator='synthetic',rationale='recovery test')
        kwargs.update(overrides)
        @asynccontextmanager
        async def project_sessions(_project_id):
            async with self.sessions() as session:
                yield session
                await session.commit()
        async def no_sync(*_args): pass
        with patch('app.project_db.project_db_session_scope',project_sessions), patch('app.project_db.push_runtime_to_master',no_sync):
            return await self.supervisor.recover_node(**kwargs)

    async def test_one_reservation_claim_and_restart_never_repeats_action(self):
        await self.exhausted_fixture()
        first=await self.retry()
        await reload_permissions(self.sessions)
        replay=await self.retry()
        self.assertEqual(first['action_id'],replay['action_id'])
        self.assertTrue(replay['idempotent_replay'])
        with self.assertRaisesRegex(ValueError,'already consumed'):
            await self.retry(idempotency_key='different-retry-0002')
        async with self.sessions() as session:
            project=await session.get(Project,7)
            actual=await claim(observation._observer,session,project,'n1','random-attempt',execution_id='exec-durable')
            self.assertEqual(actual,first['new_attempt_id'])
            await reload_permissions(self.sessions)
            with self.assertRaisesRegex(RuntimeError,'already consumed'):
                await claim(observation._observer,session,project,'n1','other-attempt',execution_id='exec-durable')
            node=await session.scalar(select(NodeRun).where(NodeRun.node_key=='n1'))
            self.assertEqual(node.attempts,9)
            self.assertEqual(project.meta['step_failure']['total_fails']['enriching_1'],9)

    async def test_old_recovery_ticket_cannot_claim_new_execution_attempt(self):
        await self.exhausted_fixture()
        await self.retry()
        async with self.sessions() as session:
            project=await session.get(Project,7)
            actual=await claim(observation._observer,session,project,'n1','fresh-attempt',execution_id='new-execution')
            self.assertEqual(actual,'fresh-attempt')
            row=await session.scalar(select(RecoveryAction))
            self.assertEqual(row.status,'queued')

    async def test_user_stop_denies_without_reservation(self):
        await self.exhausted_fixture()
        async with self.sessions() as s:
            p=await s.get(Project,7);p.meta={**p.meta,'user_stop':True};await s.commit()
        with self.assertRaisesRegex(ValueError,'user_stop'): await self.retry()
        await self.assert_no_dispatch()

    async def test_hitl_denies_without_reservation(self):
        await self.exhausted_fixture()
        async with self.sessions() as s:
            n=await s.scalar(select(NodeRun).where(NodeRun.node_key=='n2'))
            n.status=NodeRunStatus.waiting_hitl
            await s.commit()
        with self.assertRaisesRegex(ValueError,'HITL'): await self.retry()
        await self.assert_no_dispatch()

    async def test_audit_failure_denies_without_reservation(self):
        await self.exhausted_fixture()
        observation._observer.failures['exec-durable']='synthetic audit write failure'
        with self.assertRaisesRegex(ValueError,'audit/diagnosis'): await self.retry()
        await self.assert_no_dispatch()

    async def test_partial_apply_denies_without_reservation(self):
        await self.exhausted_fixture()
        async with self.sessions() as s:
            e=await s.get(observation.ObservationEvent,5)
            e.kind='apply_input'
            await s.commit()
        with self.assertRaisesRegex(ValueError,'partial apply'): await self.retry()
        await self.assert_no_dispatch()

    async def test_close_refuses_ticket_awaiting_verification(self):
        await self.exhausted_fixture()
        ticket=await self.retry()
        async with self.sessions() as s:
            row=await s.get(RecoveryAction,ticket['action_id']);row.status='await_verify'
            n=await s.scalar(select(NodeRun).where(NodeRun.node_key=='n1'))
            n.status=NodeRunStatus.done
            await s.commit()
        with self.assertRaisesRegex(ValueError,'awaiting verification'):
            await self.supervisor.close(project_id=7,execution_id='exec-durable',initiator='test',
                rationale='synthetic close',idempotency_key='close-await-verify')
        async with self.sessions() as s:
            self.assertTrue((await s.get(ActionExecutionControl,'exec-durable')).enabled)

    async def test_real_recovery_endpoint_denies_and_audits_before_dispatch(self):
        import httpx
        from fastapi import FastAPI
        from app.web.routers import observations as api
        await self.exhausted_fixture()
        async with self.sessions() as s:
            p=await s.get(Project,7);p.meta={**p.meta,'user_stop':True};await s.commit()
        app=FastAPI();app.include_router(api.router,prefix='/api')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
            with patch('app.project_db.project_db_session_scope', lambda _id: self.sessions()):
                response=await client.post('/api/projects/7/observations/exec-durable/actions/retry',json={
                    'incident_id':'source-incident','node_key':'n1','rationale':'synthetic endpoint denial',
                    'idempotency_key':'api-denial-test-0001'})
            self.assertEqual(response.status_code,409,response.text)
            self.assertIn('user_stop',response.text)
            state=await client.get('/api/projects/7/observations/exec-durable/actions')
            self.assertEqual(state.status_code,200)
            self.assertTrue(any(r['status']=='rejected' and r['action_kind']=='recovery_denied' for r in state.json()['records']))
        await self.assert_no_dispatch()

    async def test_verify_endpoint_records_matching_runtime_completion(self):
        import httpx
        from fastapi import FastAPI
        from app.web.routers import observations as api
        from app.web.deps import get_project_session
        await self.exhausted_fixture()
        async with self.sessions() as s:
            n=await s.scalar(select(NodeRun).where(NodeRun.node_key=='n1'))
            n.status=NodeRunStatus.done
            s.add(observation.ObservationEvent(event_id=str(uuid.uuid4()),execution_id='exec-durable',
                project_id=7,workflow_run_id=11,node_key='n1',attempt_id='source-attempt',
                db_scope='project',kind='node_end',payload={},created_at=datetime.utcnow()))
            await s.commit()
        app=FastAPI();app.include_router(api.router,prefix='/api')
        async def dependency():
            async with self.sessions() as s: yield s
        app.dependency_overrides[get_project_session]=dependency
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
            response=await client.post('/api/projects/7/observations/exec-durable/actions/verify',json={
                'node_key':'n1','rationale':'synthetic runtime completion','idempotency_key':'verify-endpoint-0001'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['status'],'verified')
        self.assertFalse(response.json()['verification']['semantic_quality_verified'])

    async def test_recovery_first_http_body_is_checked_before_transport(self):
        from app.services.actions_recovery import check_body
        from types import SimpleNamespace
        await self.exhausted_fixture()
        ticket=await self.retry()
        ctx=SimpleNamespace(observer=observation._observer,scope=SimpleNamespace(execution_id='exec-durable',observer_state='active',gaps=[]),
            attempt_id=ticket['new_attempt_id'],last_request_id=None)
        await check_body(ctx,digest({'test':1}))
        with self.assertRaisesRegex(RuntimeError,'before transport'):
            await check_body(ctx,digest({'test':2}))

    async def test_legacy_chat_and_fix_bugs_cannot_bypass_durable_permission(self):
        import httpx
        from fastapi import FastAPI
        from app.web.routers import db_browser as api
        from app.web.deps import get_project_session
        await self.exhausted_fixture()
        clear_permission(7)  # Simulate volatile registry loss, not revocation.
        app=FastAPI();app.include_router(api.router,prefix='/api')
        async def dependency():
            async with self.sessions() as session: yield session
        app.dependency_overrides[get_project_session]=dependency
        with patch('app.services.gpt_client.get_gpt_client',side_effect=AssertionError('model must not be called')) as model:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
                for fix_bugs in (False,True):
                    response=await client.post('/api'+api.router.prefix+'/projects/7/orchestrator/chat',
                        json={'message':'synthetic policy bypass test','history':[],'fix_bugs':fix_bugs})
                    self.assertEqual(response.status_code,409,response.text)
            model.assert_not_called()

    async def test_uncertain_request_denies_without_reservation(self):
        await self.exhausted_fixture()
        async with self.sessions() as s:
            e=await s.get(observation.ObservationEvent,3);e.payload={'complete':False};await s.commit()
        with self.assertRaisesRegex(ValueError,'uncertain'): await self.retry()
        await self.assert_no_dispatch()

    async def test_stale_graph_denies_without_reservation(self):
        await self.exhausted_fixture()
        async with self.sessions() as s:
            run=await s.get(WorkflowRun,11);run.edges_snapshot=[{'source':'n1','target':'n2'}];await s.commit()
        with self.assertRaisesRegex(ValueError,'graph revision'): await self.retry()
        await self.assert_no_dispatch()

    async def test_stale_input_config_denies_without_reservation(self):
        await self.exhausted_fixture()
        async with self.sessions() as s:
            p=await s.get(Project,7);p.gpt_text_overrides={'enrich_1':'changed'};await s.commit()
        with self.assertRaisesRegex(ValueError,'input/config'): await self.retry()
        await self.assert_no_dispatch()

    async def test_out_of_scope_denies_without_reservation(self):
        await self.exhausted_fixture()
        async with self.sessions() as s:
            i=await s.get(Incident,'source-incident');i.node_key='n2';await s.commit()
        with self.assertRaisesRegex(ValueError,'outside recovery scope'): await self.retry(node_key='n2')
        await self.assert_no_dispatch()

    async def assert_no_dispatch(self):
        async with self.sessions() as s:
            self.assertIsNone(await s.scalar(select(RecoveryAction)))
            n=await s.scalar(select(NodeRun).where(NodeRun.node_key=='n1'))
            self.assertEqual(n.status,NodeRunStatus.failed)
            self.assertEqual(n.attempts,9)


class RecoveryCapabilityAdmission(unittest.IsolatedAsyncioTestCase):
    # Reuse isolated persistence fixtures without inheriting unrelated test cases.
    asyncSetUp = DurableActions.asyncSetUp
    asyncTearDown = DurableActions.asyncTearDown
    exhausted_fixture = DurableActions.exhausted_fixture
    retry = DurableActions.retry
    assert_no_dispatch = DurableActions.assert_no_dispatch

    async def mixed_recovery_fixture(self, node_type, *, marker=None, grant=None):
        from app.services.node_capabilities import capability_for

        data = {'groupId': 'g1'}
        if marker is not None:
            data['sd_agent'] = marker
        self.nodes = [{'id': 'n1', 'type': node_type, 'data': data},
                      {'id': 'n2', 'type': 'excel_gpt', 'data': {'groupId': 'g1'}}]
        self.graph = revisions.graph_version(self.nodes, [])
        async with self.sessions() as session:
            (await session.get(Workflow, 9)).nodes = self.nodes
            (await session.get(WorkflowRun, 11)).nodes_snapshot = self.nodes
            (await session.get(ExecutionControl, 'exec-durable')).graph_version = self.graph
            for node in (await session.scalars(select(NodeRun))).all():
                node.node_type = node_type if node.node_key == 'n1' else 'excel_gpt'
            await session.commit()
        await self.exhausted_fixture(node_keys=['n1', 'n2'])
        # A second structured node gives the mixed scope its persisted retry grant.
        actions = list(capability_for('excel_gpt').actions) if grant is None else grant
        async with self.sessions() as session:
            session.add(ManagedLaunch(launch_id='mixed-launch', execution_id='exec-durable',
                project_id=7, workflow_run_id=11, idempotency_key='mixed-launch-key',
                request_hash=digest(self.nodes), policy={'allowed_actions': actions},
                entry_node='n2', status='submitted', result={}))
            await session.commit()

    async def approved_profile_fixture(self):
        from app.services.profiles_v1 import CATALOG, catalog_hash

        async with self.sessions() as session:
            project = await session.get(Project, 7)
            revision = await input_revision(session, project, 'n1')
            candidate = dict(CATALOG['synthetic-json-v1'])
            session.add(ProfileOverlay(overlay_id='capability-profile', execution_id='exec-durable',
                project_id=7, node_key='n1', source_attempt_id='source-attempt',
                profile_id='synthetic-json-v1', base_version=revision, graph_version=self.graph,
                catalog_hash=catalog_hash(), candidate_hash=digest(candidate), candidate=candidate,
                source_body_hash=digest({'test': 1}), expected_body_hash=digest({'test': 2}),
                initiator='synthetic', rationale='capability admission test', status='approved', history=[]))
            await session.commit()
        return 'capability-profile'

    async def recovery_side_effects(self):
        async with self.sessions() as session:
            state = {}
            for model in (Project, NodeRun, ActionExecutionControl, ProfileOverlay,
                          observation.ObservationEvent):
                rows = (await session.scalars(select(model).order_by(*model.__table__.primary_key.columns))).all()
                state[model.__tablename__] = [
                    {column.name: getattr(row, column.name) for column in model.__table__.columns}
                    for row in rows]
            return digest(state)

    async def assert_retry_denied(self, node_type, *, marker=None, profile=False, grant=None,
                                 reason='capability'):
        from app.services.actions_recovery import restricted_node
        from app.services.managed_launch import allowed_actions
        from app.services.profiles_v1 import reservation

        await self.mixed_recovery_fixture(node_type, marker=marker, grant=grant)
        overlay_id = await self.approved_profile_fixture() if profile else None
        action_type = 'retry_profile' if profile else 'retry_same'
        if grant is None:
            async with self.sessions() as session:
                self.assertIn(action_type, await allowed_actions(
                    session, 'exec-durable', {'retry_same', 'retry_profile'}))
        before = await self.recovery_side_effects()
        permission = get_permission(7)
        self.assertIsNone(restricted_node(7))
        with patch('app.services.node_status_machine.start_node_running', wraps=start_node_running) as dispatch, \
             patch('app.services.profiles_v1.reservation', wraps=reservation) as reserve_profile, \
             patch('app.services.gpt_client.get_gpt_client', side_effect=AssertionError('no model start')) as model:
            with self.assertRaisesRegex(ValueError, reason):
                await self.retry(profile_overlay_id=overlay_id)
            dispatch.assert_not_called()
            reserve_profile.assert_not_awaited()
            model.assert_not_called()
        await self.assert_no_dispatch()
        self.assertEqual(await self.recovery_side_effects(), before)
        self.assertEqual(get_permission(7), permission)
        self.assertIsNone(restricted_node(7))

    # plan/script were denied here until their native executors were shown sound for the
    # bounded recovery. Their admission (retry_same/retry_profile, no inner json_retry, body
    # identity modulo attachment stamps) is asserted in tests/observe_v1/test_text_nodes_recovery.py;
    # split/image_prompts stay denied below.
    async def test_retry_same_denies_native_split_with_inherited_grant(self):
        await self.assert_retry_denied('split')

    async def test_retry_profile_denies_native_split_with_inherited_grant(self):
        await self.assert_retry_denied('split', profile=True)

    # sd_agent is managed per type and admitted per agent: a refused agent (camera) never enters a managed
    # scope at all, so it has no inherited grant to retry with (the refusal is the denial).
    async def test_retry_same_denies_marked_scene_with_inherited_grant(self):
        with self.assertRaisesRegex(ValueError, "scene agent 'camera' is not admitted"):
            await self.mixed_recovery_fixture('excel_gpt', marker='camera')

    async def test_retry_profile_denies_marked_scene_with_inherited_grant(self):
        with self.assertRaisesRegex(ValueError, "scene agent 'camera' is not admitted"):
            await self.mixed_recovery_fixture('excel_gpt', marker='camera')
        await self.assert_retry_denied('excel_gpt', marker='assemble', profile=True)

    async def test_retry_same_denies_scene_assembler_with_inherited_grant(self):
        await self.assert_retry_denied('excel_gpt', marker='assemble')

    async def test_retry_profile_denies_scene_assembler_with_inherited_grant(self):
        await self.assert_retry_denied('excel_gpt', marker='assemble', profile=True)

    async def test_retry_same_denies_image_prompts_with_inherited_grant(self):
        await self.assert_retry_denied('image_prompts')

    async def test_retry_profile_denies_image_prompts_with_inherited_grant(self):
        await self.assert_retry_denied('image_prompts', profile=True)

    async def test_retry_same_denies_unsupported_node_with_inherited_grant(self):
        await self.assert_retry_denied('future-unsupported')

    async def test_retry_profile_denies_unsupported_node_with_inherited_grant(self):
        await self.assert_retry_denied('future-unsupported', profile=True)

    async def test_retry_same_still_requires_execution_grant_for_structured_excel(self):
        await self.assert_retry_denied('excel_gpt', grant=['retry_profile'], reason='execution grant')

    async def test_retry_profile_still_requires_execution_grant_for_structured_excel(self):
        await self.assert_retry_denied('excel_gpt', profile=True, grant=['retry_same'], reason='execution grant')

    async def assert_persisted_admission(self, *, profile=False, node_type='excel_gpt'):
        from app.services.node_capabilities import capability_for

        await self.mixed_recovery_fixture(node_type)
        overlay_id = await self.approved_profile_fixture() if profile else None
        ticket = await self.retry(profile_overlay_id=overlay_id)
        async with self.sessions() as session:
            row = await session.get(RecoveryAction, ticket['action_id'])
            self.assertEqual(row.preconditions['effective_node_type'], node_type)
            self.assertEqual(row.preconditions['capability_actions'], list(capability_for(node_type).actions))
            self.assertEqual(row.preconditions['action_type'], 'retry_profile' if profile else 'retry_same')
            self.assertEqual(row.preconditions, ticket['preconditions'])

    async def test_structured_excel_retry_same_persists_capability_admission(self):
        await self.assert_persisted_admission()

    async def test_structured_excel_retry_profile_persists_capability_admission(self):
        await self.assert_persisted_admission(profile=True)


class LegacyQueuedRecoveryClaims(unittest.IsolatedAsyncioTestCase):
    # Old queued rows must be exercised without passing through new admission.
    asyncSetUp = DurableActions.asyncSetUp
    asyncTearDown = DurableActions.asyncTearDown
    exhausted_fixture = DurableActions.exhausted_fixture
    mixed_recovery_fixture = RecoveryCapabilityAdmission.mixed_recovery_fixture
    approved_profile_fixture = RecoveryCapabilityAdmission.approved_profile_fixture
    recovery_side_effects = RecoveryCapabilityAdmission.recovery_side_effects

    async def legacy_queued_fixture(self, node_type, *, marker=None, profile=False):
        from app.models import ProjectStatus
        from app.orchestrator.node_registry import spec_for_node
        from app.services.actions_v1 import ActionRecord
        from app.services.actions_recovery import finish_job_tracking, restricted_node

        await self.mixed_recovery_fixture(node_type, marker=marker)
        overlay_id = await self.approved_profile_fixture() if profile else None
        async with self.sessions() as session:
            project = await session.get(Project, 7)
            spec = spec_for_node(self.nodes[0])
            project.status = spec.running_status if spec else ProjectStatus.enriching_1
            project.meta = {**project.meta, 'step_failure': {
                'total_fails': {project.status.value: 9}, 'last_running': project.status.value,
                'abandoned_at': 'synthetic'}}
            node = await session.scalar(select(NodeRun).where(NodeRun.node_key == 'n1'))
            node.status = NodeRunStatus.running
            await session.flush()
            revision = await input_revision(session, project, 'n1')
            preconditions = {'native_failures': 9, 'running_status': project.status.value,
                'input_revision': revision, 'graph_version': self.graph,
                'first_body_hash': digest({'test': 1}), 'prior_attempt_terminal': True,
                'no_partial_apply': True, 'user_stop': False, 'node_scope_authorized': True}
            if profile:
                overlay = await session.get(ProfileOverlay, overlay_id)
                overlay.base_version = revision
                overlay.attempt_id = 'legacy-attempt'
                overlay.status = 'reserved'
                overlay.history = [{'kind': 'recovery_reserved', 'action_id': 'legacy-recovery',
                                    'attempt_id': 'legacy-attempt'}]
                # Historical profile tickets already recorded the strategy and overlay.
                preconditions.update(action_type='retry_profile', profile_overlay_id=overlay_id,
                    candidate_hash=overlay.candidate_hash, source_body_hash=overlay.source_body_hash,
                    first_body_hash=overlay.expected_body_hash)
            self.assertNotIn('effective_node_type', preconditions)
            self.assertNotIn('capability_actions', preconditions)
            if not profile:
                self.assertNotIn('action_type', preconditions)
            session.add(RecoveryAction(action_id='legacy-recovery', execution_id='exec-durable',
                project_id=7, incident_id='source-incident', node_key='n1',
                source_attempt_id='source-attempt', new_attempt_id='legacy-attempt',
                idempotency_key='legacy-ticket-key', preconditions=preconditions,
                budget_state={'request_limit': 20, 'requests_used': 0,
                              'recovery_limit': 1, 'recoveries_reserved': 1},
                status='queued', result={}, verification_result={}))
            session.add(ActionRecord(action_id='legacy-recovery', execution_id='exec-durable',
                project_id=7, action_kind='node_profile_recovery' if profile else 'node_recovery',
                node_key='n1', attempt_id='legacy-attempt', initiator='synthetic',
                rationale='legacy queued recovery fixture', evidence_ids=[],
                preconditions=dict(preconditions), idempotency_key='legacy-ticket-key',
                status='queued', verification={}))
            await session.commit()
        # Recreate volatile state from the persisted submitted launch and ticket.
        clear_permission(7)
        finish_job_tracking(7)
        await reload_permissions(self.sessions)
        self.assertTrue(get_permission(7).enabled)
        self.assertFalse(get_permission(7).held)
        self.assertEqual(restricted_node(7), 'n1')
        async with self.sessions() as session:
            scope = await session.get(ExecutionControl, 'exec-durable')
            control = await session.get(ActionExecutionControl, 'exec-durable')
            run = await session.get(WorkflowRun, 11)
            ticket = await session.get(RecoveryAction, 'legacy-recovery')
            project = await session.get(Project, 7)
            self.assertEqual(ticket.preconditions['graph_version'], revisions.graph_version(run.nodes_snapshot, run.edges_snapshot))
            self.assertEqual(ticket.preconditions['graph_version'], control.graph_version)
            self.assertEqual(ticket.preconditions['input_revision'],
                             await input_revision(session, project, 'n1'))
        observation._observer.scopes = {11: scope}

    async def claim_persistence(self):
        from app.services.actions_v1 import ActionRecord

        state = {'business': await self.recovery_side_effects()}
        async with self.sessions() as session:
            for model in (RecoveryAction, ActionRecord):
                rows = (await session.scalars(select(model).order_by(
                    *model.__table__.primary_key.columns))).all()
                state[model.__tablename__] = [
                    {column.name: getattr(row, column.name) for column in model.__table__.columns}
                    for row in rows]
        return digest(state)

    async def assert_legacy_claim_denied(self, node_type, *, marker=None, profile=False,
                                       through_worker=False):
        from app.services.actions_recovery import _inflight_projects, restricted_node
        from app.services.profiles_v1 import load_attempt

        await self.legacy_queued_fixture(node_type, marker=marker, profile=profile)
        before = await self.claim_persistence()
        permission = get_permission(7)
        body = AsyncMock()
        self.assertNotIn(7, _inflight_projects)
        with patch('app.services.profiles_v1.load_attempt', wraps=load_attempt) as load_profile, \
             patch('app.services.gpt_client.get_gpt_client', side_effect=AssertionError('no model start')) as model, \
             patch('app.services.node_capabilities.observe_project_text_result', new=AsyncMock()), \
             patch('app.services.node_capabilities.observe_split_result', new=AsyncMock()), \
             patch('app.services.node_capabilities.observe_image_prompt_result', new=AsyncMock()):
            async with self.sessions() as session:
                project = await session.get(Project, 7)
                with self.assertRaisesRegex(RuntimeError, 'capability'):
                    if through_worker:
                        async with observation.observe_attempt(session, project):
                            await body()
                    else:
                        await claim(observation._observer, session, project, 'n1',
                                    'ordinary-attempt', execution_id='exec-durable')
                self.assertNotIn('recovery_attempt_id', session.info)
            body.assert_not_awaited()
            load_profile.assert_not_awaited()
            model.assert_not_called()
        self.assertEqual(await self.claim_persistence(), before)
        self.assertEqual(get_permission(7), permission)
        self.assertEqual(restricted_node(7), 'n1')
        self.assertNotIn(7, _inflight_projects)

    # plan/script now carry retry capabilities (they cannot have legacy denied tickets);
    # a queued ticket for them follows the same single-claim rule as excel_gpt.
    async def test_old_retry_same_plan_claims_once_after_restart(self):
        await self.assert_old_excel_claims_once(node_type='plan')

    async def test_old_retry_profile_plan_claims_once_after_restart(self):
        await self.assert_old_excel_claims_once(profile=True, node_type='plan')

    async def test_old_retry_same_script_claims_once_after_restart(self):
        await self.assert_old_excel_claims_once(node_type='script')

    async def test_old_retry_profile_script_claims_once_after_restart(self):
        await self.assert_old_excel_claims_once(profile=True, node_type='script')

    async def test_old_retry_same_split_claim_denied_after_restart(self):
        await self.assert_legacy_claim_denied('split')

    async def test_old_retry_profile_split_claim_denied_after_restart(self):
        await self.assert_legacy_claim_denied('split', profile=True)

    async def test_old_retry_same_image_prompts_claim_denied_after_restart(self):
        await self.assert_legacy_claim_denied('image_prompts')

    async def test_old_retry_profile_image_prompts_claim_denied_after_restart(self):
        await self.assert_legacy_claim_denied('image_prompts', profile=True)

    async def test_old_retry_same_marked_scene_claim_denied_after_restart(self):
        await self.assert_legacy_claim_denied('excel_gpt', marker='assemble')

    async def test_old_retry_profile_marked_scene_claim_denied_after_restart(self):
        await self.assert_legacy_claim_denied('excel_gpt', marker='assemble', profile=True)

    async def test_old_retry_same_scene_assembler_claim_denied_after_restart(self):
        await self.assert_legacy_claim_denied('excel_gpt', marker='assemble')

    async def test_old_retry_profile_scene_assembler_claim_denied_after_restart(self):
        await self.assert_legacy_claim_denied('excel_gpt', marker='assemble', profile=True)

    async def test_old_retry_same_unsupported_claim_denied_after_restart(self):
        await self.assert_legacy_claim_denied('future-unsupported')

    async def test_old_retry_profile_unsupported_claim_denied_after_restart(self):
        await self.assert_legacy_claim_denied('future-unsupported', profile=True)

    async def test_old_native_retry_same_cannot_enter_worker_body_after_restart(self):
        await self.assert_legacy_claim_denied('split', through_worker=True)

    async def test_old_native_retry_profile_cannot_activate_profile_or_enter_body_after_restart(self):
        await self.assert_legacy_claim_denied('split', profile=True, through_worker=True)

    async def assert_old_excel_claims_once(self, *, profile=False, node_type='excel_gpt'):
        from app.services.actions_v1 import ActionRecord

        await self.legacy_queued_fixture(node_type, profile=profile)
        before_business = await self.recovery_side_effects()
        async with self.sessions() as session:
            project = await session.get(Project, 7)
            actual = await claim(observation._observer, session, project, 'n1',
                                 'ordinary-attempt', execution_id='exec-durable')
        self.assertEqual(actual, 'legacy-attempt')
        async with self.sessions() as session:
            row = await session.get(RecoveryAction, 'legacy-recovery')
            record = await session.get(ActionRecord, 'legacy-recovery')
            self.assertEqual(row.status, 'claimed')
            self.assertTrue(row.result['worker_claimed'])
            self.assertEqual(record.status, 'running')
            self.assertEqual(len((await session.scalars(select(RecoveryAction))).all()), 1)
        self.assertEqual(await self.recovery_side_effects(), before_business)
        await reload_permissions(self.sessions)
        after_claim = await self.claim_persistence()
        async with self.sessions() as session:
            project = await session.get(Project, 7)
            with self.assertRaisesRegex(RuntimeError, 'already consumed'):
                await claim(observation._observer, session, project, 'n1',
                            'second-attempt', execution_id='exec-durable')
        self.assertEqual(await self.claim_persistence(), after_claim)

    async def test_old_excel_retry_same_without_new_proof_claims_once_after_restart(self):
        await self.assert_old_excel_claims_once()

    async def test_old_excel_retry_profile_without_new_capability_proof_claims_once_after_restart(self):
        await self.assert_old_excel_claims_once(profile=True)


if __name__ == "__main__":
    unittest.main()
