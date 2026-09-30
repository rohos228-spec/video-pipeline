"""Owner policy and one durable allowance per workflow run; no HTTP transport."""
import asyncio
import unittest
import uuid
from unittest.mock import patch
from sqlalchemy import select
from app.services import decision_config,observation
from app.services.actions_v1 import ActionExecutionControl,ActionSupervisor,reload_permissions,get_permission
from test_actions_v1_contract import DurableActions


class OwnerBudget(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=DurableActions.asyncSetUp
    asyncTearDown=DurableActions.asyncTearDown

    async def enable(self,execution='exec-durable',limit=12):
        return await self.supervisor.enable(project_id=7,execution_id=execution,group_id='g1',node_keys=['n1'],
            graph_version=self.graph,initiator='owner-test',rationale='explicit synthetic owner allowance',request_limit=limit)

    def config(self):
        cfg=decision_config.runtime_config()
        return {**cfg,'budget_policy':{'policy_id':'offline-scale-1000','max_requests':1000}}

    async def new_scope(self):
        async with self.sessions() as s:
            old=await s.get(observation.ExecutionControl,'exec-durable')
            s.add(observation.ExecutionControl(execution_id='new-execution',project_id=7,workflow_run_id=11,
                node_keys=list(old.node_keys),graph_version=self.graph,mode='observe',observer_state='active',cursors={},gaps=[]))
            await s.commit()

    async def test_default_policy_still_denies_101(self):
        with self.assertRaises(ValueError):await self.enable(limit=101)
        self.assertIsNone(get_permission(7))

    async def test_explicit_owner_scale_atomic_restart_and_exhaustion(self):
        cfg=self.config()
        with patch.object(decision_config,'runtime_config',return_value=decision_config.validate_config(cfg)):
            await self.enable(limit=1000)
            values=[]
            for batch in range(100):
                values.extend(await asyncio.gather(*(self.supervisor.charge_request('exec-durable') for _ in range(10))))
                if batch==49:
                    await reload_permissions(self.sessions)
                    self.assertEqual(get_permission(7).requests_used,500)
                    self.supervisor=ActionSupervisor(self.sessions)
            self.assertEqual(sorted(values),list(range(1,1001)))
            await reload_permissions(self.sessions)
            self.assertEqual(get_permission(7).requests_used,1000)
            with self.assertRaisesRegex(RuntimeError,'budget exhausted'):
                await self.supervisor.charge_request('exec-durable')
            async with self.sessions() as s:
                self.assertEqual((await s.get(ActionExecutionControl,'exec-durable')).requests_used,1000)

    async def test_closed_execution_cannot_reset_workflow_allowance(self):
        await self.enable(limit=1)
        await self.supervisor.charge_request('exec-durable')
        async with self.sessions() as s:
            old=await s.get(ActionExecutionControl,'exec-durable');old.enabled=False;old.held=True
            await s.commit()
        await self.new_scope()
        with self.assertRaisesRegex(ValueError,'allowance|managed history'):
            await self.enable(execution='new-execution',limit=12)
        async with self.sessions() as s:
            self.assertEqual((await s.get(ActionExecutionControl,'exec-durable')).requests_used,1)
            self.assertIsNone(await s.get(ActionExecutionControl,'new-execution'))

    async def test_two_owners_cannot_create_parallel_allowances(self):
        await self.new_scope()
        results=await asyncio.gather(self.enable(),self.enable(execution='new-execution'),return_exceptions=True)
        self.assertEqual(sum(isinstance(result,dict) for result in results),1)
        async with self.sessions() as s:
            self.assertEqual(len((await s.scalars(select(ActionExecutionControl))).all()),1)

    async def test_owner_revision_change_blocks_before_charge(self):
        cfg=self.config()
        with patch.object(decision_config,'runtime_config',return_value=decision_config.validate_config(cfg)):
            await self.enable(limit=1000)
            changed={**cfg,'budget_policy':{'policy_id':'other-owner-version','max_requests':1000}}
            with patch.object(decision_config,'runtime_config',return_value=decision_config.validate_config(changed)):
                with self.assertRaisesRegex(RuntimeError,'configuration revision'):
                    await self.supervisor.charge_request('exec-durable')
            async with self.sessions() as s:self.assertEqual((await s.get(ActionExecutionControl,'exec-durable')).requests_used,0)


class OwnerPolicySchema(unittest.TestCase):
    def test_invalid_owner_policy_never_authorizes_more(self):
        base=decision_config.runtime_config()
        for policy in ({'policy_id':'x','max_requests':True},{'policy_id':'x','max_requests':0},
                       {'policy_id':'x','max_requests':100001},{'policy_id':'','max_requests':1000},
                       {'policy_id':'x','max_requests':1000,'shell':'ignored?'}):
            with self.subTest(policy=policy),self.assertRaises(ValueError):
                decision_config.validate_config({**base,'budget_policy':policy})


if __name__=='__main__':unittest.main(verbosity=2)
