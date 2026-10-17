import asyncio
import sqlite3
from pathlib import Path
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from sqlalchemy import delete, select

from app.services import revisions
from app.models import NodeRun, NodeRunStatus, Project, ProjectStatus, Workflow, WorkflowRun
from app.services import observation
from app.services.actions_v1 import ActionExecutionControl, clear_permission, get_permission
from app.services.decision_config import DecisionRuntimeBinding
from app.services.decision_state import DecisionControl
from app.web.deps import get_project_session
from app.web.routers import observations
from test_actions_v1_contract import DurableActions


class ManagedLaunchAPI(unittest.IsolatedAsyncioTestCase):
    fixture_node_type = 'excel_gpt'

    async def asyncSetUp(self):
        await DurableActions.asyncSetUp(self)
        self.nodes = [
            {'id':'draft-A','type':'excel_gpt','data':{'slot':0}},
            {'id':'report-Z','type':'excel_gpt','data':{'slot':0}},
            {'id':'outside','type':'images','data':{}},
        ]
        self.edges = [{'id':'a-z','source':'draft-A','target':'report-Z'}]
        self.graph = revisions.graph_version(self.nodes, self.edges)
        async with self.sessions() as s:
            await s.execute(delete(observation.ExecutionControl))
            await s.execute(delete(NodeRun))
            workflow = await s.get(Workflow,9)
            workflow.is_default=True; workflow.nodes=self.nodes; workflow.edges=self.edges
            run=await s.get(WorkflowRun,11)
            run.nodes_snapshot=self.nodes;run.edges_snapshot=self.edges
            project=await s.get(Project,7)
            project.meta={'canvas_graph':{'nodes':self.nodes,'edges':self.edges,'workflow_id':9}}
            s.add_all([NodeRun(workflow_run_id=11,node_key=n['id'],node_type=n['type']) for n in self.nodes])
            await s.commit()
        self.app=FastAPI()
        self.app.include_router(observations.router,prefix='/api')
        async def project_session():
            async with self.sessions() as session:
                try:yield session
                except BaseException:
                    await session.rollback()
                    raise
        self.app.dependency_overrides[get_project_session]=project_session
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://localhost')
        self.url='/api/projects/7/observations/launch'
        self.payload={'workflow_run_id':11,'graph_version':self.graph,
            'node_keys':['draft-A','report-Z'],'strategies':['wait','human_review','retry_same'],
            'request_limit':12,'rationale':'synthetic API acceptance','idempotency_key':'launch-acceptance-0001'}

    async def asyncTearDown(self):
        await self.client.aclose()
        await DurableActions.asyncTearDown(self)

    async def test_one_call_persists_all_permissions_before_native_start_and_replays(self):
        original=observations.start_step
        calls=[]
        async def inspected_start(*args,**kwargs):
            async with self.sessions() as s:
                control=await s.scalar(select(ActionExecutionControl))
                self.assertTrue(control.enabled)
                self.assertFalse(control.held)
                scope=await s.get(observation.ExecutionControl,control.execution_id)
                self.assertEqual(scope.mode,'managed')
                self.assertTrue((await s.get(DecisionControl,control.execution_id)).enabled)
                self.assertIsNotNone(await s.get(DecisionRuntimeBinding,control.execution_id))
            calls.append(kwargs.get('node_key'))
            self.assertFalse(kwargs.get('explicit_ui_start',False))
            return await original(*args,**kwargs)
        with patch.object(observations,'start_step',side_effect=inspected_start):
            first=await self.client.post(self.url,json=self.payload)
            self.assertEqual(first.status_code,201,first.text)
            self.assertEqual(first.json()['status'],'submitted')
            clear_permission(7)
            await observation.get_observer().reload()
            second=await self.client.post(self.url,json=self.payload)
            self.assertEqual(second.status_code,201,second.text)
            self.assertEqual(first.json()['execution_id'],second.json()['execution_id'])
            self.assertTrue(second.json()['idempotent_replay'])
        self.assertEqual(calls,['draft-A'])
        async with self.sessions() as s:
            rows=(await s.scalars(select(NodeRun))).all()
            self.assertEqual({r.node_key:r.status.value for r in rows},
                {'draft-A':'running','report-Z':'pending','outside':'pending'})
            self.assertEqual(len((await s.scalars(select(ActionExecutionControl))).all()),1)

    async def test_rejects_stale_revision_and_unsupported_scope_before_permissions(self):
        for changes in ({'graph_version':'0'*64},{'node_keys':['draft-A','outside']},
                        {'node_keys':['report-Z']},{'strategies':['shell']},
                        {'request_limit':101}):
            response=await self.client.post(self.url,json={**self.payload,**changes})
            self.assertIn(response.status_code,(409,422),response.text)
        async with self.sessions() as s:
            self.assertIsNone(await s.scalar(select(ActionExecutionControl)))
            self.assertTrue(all(r.status==NodeRunStatus.pending for r in (await s.scalars(select(NodeRun))).all()))
        self.assertIsNone(get_permission(7))

    async def test_dispatch_failure_leaves_durable_hold_and_cannot_be_replayed(self):
        with patch.object(observations,'start_step',side_effect=OSError('synthetic dispatch failure')) as start:
            response=await self.client.post(self.url,json=self.payload)
            self.assertEqual(response.status_code,409,response.text)
            async with self.sessions() as s:
                permission=await s.scalar(select(ActionExecutionControl))
                self.assertIsNotNone(permission)
                self.assertTrue(permission.held)
                self.assertFalse((await s.get(DecisionControl,permission.execution_id)).enabled)
                self.assertTrue(all(r.status==NodeRunStatus.pending for r in (await s.scalars(select(NodeRun))).all()))
            clear_permission(7)
            await observation.get_observer().reload()
            replay=await self.client.post(self.url,json=self.payload)
            self.assertIn(replay.status_code,(201,409),replay.text)
            self.assertEqual(start.await_count,1)
            self.assertTrue(get_permission(7).held)

    async def test_stop_and_backoff_are_never_cleared_by_launch(self):
        for flag in ({'user_stop':True},{'mass_lane_user_stop':True},
                     {'step_failure':{'sleep_until':'2099-01-01T00:00:00+00:00'}}):
            async with self.sessions() as s:
                p=await s.get(Project,7);p.meta={**p.meta,**flag};await s.commit()
            response=await self.client.post(self.url,json=self.payload)
            self.assertEqual(response.status_code,409,response.text)
            async with self.sessions() as s:
                p=await s.get(Project,7)
                for key,value in flag.items():self.assertEqual(p.meta[key],value)
                self.assertIsNone(await s.scalar(select(ActionExecutionControl)))
                p.meta={k:v for k,v in p.meta.items() if k not in flag};await s.commit()

    async def test_parallel_replay_creates_one_execution_and_changed_payload_conflicts(self):
        replies=await asyncio.gather(*(self.client.post(self.url,json=self.payload) for _ in range(3)))
        self.assertTrue(all(r.status_code==201 for r in replies),[r.text for r in replies])
        self.assertEqual(len({r.json()['execution_id'] for r in replies}),1)
        conflict=await self.client.post(self.url,json={**self.payload,'request_limit':13})
        self.assertEqual(conflict.status_code,409,conflict.text)

    async def test_launch_options_publish_revision_and_supported_nodes_without_enabling(self):
        response=await self.client.get('/api/projects/7/observations/launch-options')
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()
        self.assertEqual(data['workflow_run_id'],11)
        self.assertEqual(data['graph_version'],self.graph)
        self.assertEqual({n['node_key'] for n in data['nodes'] if n['supported']},{'draft-A','report-Z','outside'})
        native=next(n for n in data['nodes'] if n['node_key']=='outside')
        self.assertEqual(native['result_kind'],'image')
        self.assertNotIn('retry_profile',native['actions'])
        self.assertEqual(data['request_limit_max'],100)
        self.assertEqual(data['online_authorized'],False)
        async with self.sessions() as s:self.assertIsNone(await s.scalar(select(ActionExecutionControl)))

    async def test_native_scope_cannot_preapprove_an_unimplemented_recovery_strategy(self):
        from app.services.managed_launch import prepare
        request={**self.payload,'node_keys':['outside'],'strategies':['wait','human_review','retry_profile']}
        async with self.sessions() as session:
            with self.assertRaisesRegex(ValueError,'strategy.*node|node.*strategy'):
                await prepare(observation.get_observer(),session,project_id=7,request=request,initiator='test')
        async with self.sessions() as s:
            self.assertIsNone(await s.scalar(select(ActionExecutionControl)))

    async def test_interrupted_launch_is_held_on_reload_and_manual_resume_cannot_bypass(self):
        from app.services.managed_launch import prepare,ManagedLaunch
        async with self.sessions() as s:
            row,_=await prepare(observation.get_observer(),s,project_id=7,request=self.payload,initiator='test')
        clear_permission(7)
        await observation.get_observer().reload()
        self.assertTrue(get_permission(7).held)
        async with self.sessions() as s:
            self.assertEqual((await s.get(ManagedLaunch,row.launch_id)).status,'held')
            self.assertFalse((await s.get(DecisionControl,row.execution_id)).enabled)
        response=await self.client.post('/api/projects/7/observations/'+row.execution_id+'/actions/control',
            json={'held':False,'rationale':'attempt unsafe resume','idempotency_key':'unsafe-resume-key'})
        self.assertEqual(response.status_code,409,response.text)
        self.assertTrue(get_permission(7).held)

    async def test_owner_policy_failure_creates_no_partial_permission(self):
        with patch('app.services.decision_config.check_route',side_effect=ValueError('owner policy disabled')):
            response=await self.client.post(self.url,json=self.payload)
        self.assertEqual(response.status_code,409,response.text)
        async with self.sessions() as s:
            self.assertIsNone(await s.scalar(select(observation.ExecutionControl)))
            self.assertIsNone(await s.scalar(select(ActionExecutionControl)))
            self.assertIsNone(await s.scalar(select(DecisionControl)))
            self.assertIsNone(await s.scalar(select(DecisionRuntimeBinding)))
        self.assertIsNone(get_permission(7))

    async def test_explicit_owner_policy_exposes_and_binds_scale_limit(self):
        from app.services import decision_config
        from app.services.actions_v1 import ManagedRunAllowance
        cfg=decision_config.runtime_config()
        cfg={**cfg,'budget_policy':{'policy_id':'synthetic-scale-1000','max_requests':1000}}
        with patch.object(decision_config,'runtime_config',return_value=decision_config.validate_config(cfg)):
            options=await self.client.get('/api/projects/7/observations/launch-options')
            self.assertEqual(options.json()['request_limit_max'],1000)
            response=await self.client.post(self.url,json={**self.payload,'request_limit':1000})
            self.assertEqual(response.status_code,201,response.text)
            self.assertEqual(response.json()['policy']['owner_budget_policy'],cfg['budget_policy'])
            async with self.sessions() as s:
                allowance=await s.get(ManagedRunAllowance,(7,11))
                self.assertEqual(allowance.execution_id,response.json()['execution_id'])
                self.assertEqual(allowance.request_limit,1000)

    async def test_launch_request_cannot_change_owner_policy(self):
        response=await self.client.post(self.url,json={**self.payload,'budget_policy':{'max_requests':1000}})
        self.assertEqual(response.status_code,422,response.text)
        async with self.sessions() as s:self.assertIsNone(await s.scalar(select(ActionExecutionControl)))

    async def test_launch_binds_preapproved_profile_catalog_before_start(self):
        from app.services.profiles_v1 import active_catalog,catalog_hash
        options=(await self.client.get('/api/projects/7/observations/launch-options')).json()
        self.assertIn('retry_profile',options['strategies'])
        self.assertEqual(options['profile_catalog_hash'],catalog_hash())
        self.assertEqual({p['profile_id'] for p in options['profiles']},set(active_catalog()))
        response=await self.client.post(self.url,json={**self.payload,
            'strategies':['wait','human_review','retry_same','retry_profile'],
            'profile_ids':['synthetic-format-v1'],'profile_catalog_hash':catalog_hash()})
        self.assertEqual(response.status_code,201,response.text)
        policy=response.json()['policy']
        self.assertEqual(policy['profile_ids'],['synthetic-format-v1'])
        self.assertEqual(policy['profile_catalog_hash'],catalog_hash())
        self.assertEqual(policy['profiles'],{'synthetic-format-v1':active_catalog()['synthetic-format-v1']})

    async def test_unapproved_unknown_or_stale_profiles_create_no_permission(self):
        from app.services.profiles_v1 import catalog_hash
        for changes in (
            {'strategies':['retry_profile']},
            {'profile_ids':['synthetic-format-v1'],'profile_catalog_hash':catalog_hash()},
            {'strategies':['retry_profile'],'profile_ids':['foreign'],'profile_catalog_hash':catalog_hash()},
            {'strategies':['retry_profile'],'profile_ids':['synthetic-format-v1'],'profile_catalog_hash':'0'*64},
        ):
            response=await self.client.post(self.url,json={**self.payload,**changes})
            self.assertEqual(response.status_code,409,response.text)
            async with self.sessions() as s:self.assertIsNone(await s.scalar(select(ActionExecutionControl)))

    async def test_idle_project_projection_refreshed_without_changing_node_attempts(self):
        from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
        from app.services.managed_launch import prepare
        replica=Path(self.tmp.name)/'project.db'
        with sqlite3.connect(Path(self.tmp.name)/'master.db') as source,sqlite3.connect(replica) as target:
            source.backup(target)
        engine=create_async_engine('sqlite+aiosqlite:///'+str(replica))
        sessions=async_sessionmaker(engine,expire_on_commit=False)
        try:
            async with sessions() as s:
                project=await s.get(Project,7);project.status=ProjectStatus.script_ready;await s.commit()
            async with sessions() as s:
                launch,replay=await prepare(observation.get_observer(),s,project_id=7,request=self.payload,initiator='test')
                self.assertFalse(replay)
            async with self.sessions() as s:
                self.assertEqual((await s.get(Project,7)).status,ProjectStatus.script_ready)
                self.assertEqual(launch.policy['initial_status_projection']['from'],'new')
                self.assertEqual(launch.policy['initial_status_projection']['source'],'project_db')
                nodes=(await s.scalars(select(NodeRun))).all()
                self.assertTrue(all(node.status==NodeRunStatus.pending and node.attempts==0 for node in nodes))
        finally:await engine.dispose()


if __name__=='__main__':unittest.main(verbosity=2)
