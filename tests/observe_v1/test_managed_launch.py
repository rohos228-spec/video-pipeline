"""Unified launch API contracts; isolated SQLite, no worker or provider here.

The worker acceptance is separate. These tests exercise permission persistence,
the real initial FSM transition, rejection and replay through the ASGI API.
"""
import asyncio
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from sqlalchemy import delete, select

from app.models import NodeRun, NodeRunStatus, Project, Workflow, WorkflowRun
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
            {'id':'outside','type':'script','data':{}},
        ]
        self.edges = [{'id':'a-z','source':'draft-A','target':'report-Z'}]
        self.graph = observation.digest({'nodes':self.nodes,'edges':self.edges})
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


if __name__=='__main__':unittest.main(verbosity=2)
