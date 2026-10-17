import hashlib
import importlib
import unittest
from datetime import datetime
from unittest.mock import AsyncMock, patch
from contextlib import asynccontextmanager

from sqlalchemy import select, text

import test_actions_v1_contract as actions_tests
from app.services import kill_switch, observation
from app.services.actions_recovery import RecoveryAction
from app.services.actions_v1 import (ActionExecutionControl, ActionRecord, clear_permission, evaluate_start,
                                     get_permission, reload_permissions, worker_entry_admission)
from app.models import NodeRun, NodeRunStatus
from app.web.routers import observations as router


class KillSwitchBase(actions_tests.DurableActions):
    async def asyncSetUp(self):
        kill_switch.reset_cache()
        await super().asyncSetUp()

    async def asyncTearDown(self):
        kill_switch.reset_cache()
        await super().asyncTearDown()

    async def enable(self, limit=8):
        await self.supervisor.enable(project_id=7, execution_id='exec-durable', group_id='g1', node_keys=['n1'],
            graph_version=self.graph, initiator='test-operator', rationale='kill switch fixture', request_limit=limit)

    async def switch(self, active, actor='test-operator', rationale='emergency stop test'):
        return await kill_switch.set_state(self.sessions, active=active, actor=actor, rationale=rationale)


class KillSwitchState(KillSwitchBase):
    async def test_default_is_off_and_requires_identity_and_rationale(self):
        self.assertFalse((await kill_switch.status(self.sessions))['active'])
        for actor, rationale in (('', 'valid rationale'), ('operator', ''), ('operator', 'x')):
            with self.subTest(actor=actor, rationale=rationale), self.assertRaises(ValueError):
                await kill_switch.set_state(self.sessions, active=True, actor=actor, rationale=rationale)
        self.assertFalse((await kill_switch.status(self.sessions))['active'])

    async def test_flag_is_durable_across_restart_and_off_restores(self):
        await self.enable()
        result = await self.switch(True)
        self.assertTrue(result['changed'])
        self.assertTrue(kill_switch.cached_active())
        kill_switch.reset_cache()
        clear_permission(7)
        self.assertFalse(kill_switch.cached_active())
        await reload_permissions(self.sessions)
        self.assertTrue(kill_switch.cached_active())
        self.assertTrue((await kill_switch.status(self.sessions))['active'])
        await self.switch(False)
        kill_switch.reset_cache()
        await reload_permissions(self.sessions)
        self.assertFalse(kill_switch.cached_active())

    async def test_unchanged_value_is_a_noop_without_audit_noise(self):
        await self.enable()
        first = await self.switch(True)
        again = await self.switch(True, rationale='second identical request')
        self.assertFalse(again['changed'])
        self.assertEqual(first['revision'], again['revision'])
        async with self.sessions() as s:
            rows = (await s.scalars(select(ActionRecord).where(ActionRecord.action_kind == 'kill_switch_on'))).all()
            self.assertEqual(len(rows), 1)

    async def test_audit_history_and_per_execution_action_records(self):
        await self.enable()
        await self.switch(True, actor='web_operator:abc', rationale='provider billing incident')
        await self.switch(False, actor='web_operator:abc', rationale='resolved with provider')
        status = await kill_switch.status(self.sessions)
        self.assertEqual([h['active'] for h in status['history']], [False, True])
        self.assertEqual(status['history'][1]['actor'], 'web_operator:abc')
        async with self.sessions() as s:
            records = (await s.scalars(select(ActionRecord).where(
                ActionRecord.execution_id == 'exec-durable',
                ActionRecord.action_kind.in_(['kill_switch_on', 'kill_switch_off'])).order_by(ActionRecord.created_at))).all()
            self.assertEqual([r.action_kind for r in records], ['kill_switch_on', 'kill_switch_off'])
            self.assertEqual(records[0].initiator, 'web_operator:abc')
            self.assertEqual(records[0].rationale, 'provider billing incident')
            self.assertFalse(records[0].verification['business_action'])

    async def test_missing_table_reads_off_and_set_recreates_it(self):
        async with self.engine.begin() as conn:
            await conn.execute(text('DROP TABLE supervisor_kill_switch'))
        async with self.sessions() as s:
            self.assertFalse(await kill_switch.is_active(s))
        self.assertTrue((await self.switch(True))['changed'])
        async with self.sessions() as s:
            self.assertTrue(await kill_switch.is_active(s))


class KillSwitchChokePoints(KillSwitchBase):
    async def test_charge_request_refuses_before_reservation_and_resumes_when_off(self):
        await self.enable(limit=3)
        self.assertEqual(await self.supervisor.charge_request('exec-durable'), 1)
        await self.switch(True)
        with self.assertRaises(kill_switch.KillSwitchActive):
            await self.supervisor.charge_request('exec-durable')
        async with self.sessions() as s:
            self.assertEqual((await s.get(ActionExecutionControl, 'exec-durable')).requests_used, 1)
        self.assertEqual(get_permission(7).requests_used, 1)
        await self.switch(False)
        self.assertEqual(await self.supervisor.charge_request('exec-durable'), 2)

    async def test_worker_admission_stops_managed_project_only_and_before_business_session(self):
        from app.services.advance_runner import advance_project_job
        await self.enable()
        self.assertTrue((await worker_entry_admission(7)).allowed)
        await self.switch(True)
        decision = await worker_entry_admission(7)
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, 'kill_switch_active')
        self.assertTrue((await worker_entry_admission(8)).allowed, 'unmanaged projects are not supervisory work')
        for restarted in (False, True):
            if restarted:
                clear_permission(7)
            with patch('app.services.advance_runner.project_db_session_scope',
                       side_effect=AssertionError('stopped worker must not open business session')):
                result = await advance_project_job(7, None)
            self.assertIsNone(result.new_status)
        await self.switch(False)
        self.assertTrue((await worker_entry_admission(7)).allowed or get_permission(7) is None)

    async def test_start_gate_refuses_while_stopped(self):
        await self.enable()
        permission = get_permission(7)
        self.assertEqual(evaluate_start(permission, project_id=7, node_key='n1', graph_version=self.graph).reason, 'authorized')
        await self.switch(True)
        self.assertEqual(evaluate_start(permission, project_id=7, node_key='n1', graph_version=self.graph).reason,
                         'kill_switch_active')

    async def test_recovery_is_refused_before_any_ticket_or_node_change(self):
        await self.exhausted_fixture()
        await self.switch(True)
        with self.assertRaisesRegex(ValueError, 'kill switch'):
            await self.retry()
        async with self.sessions() as s:
            self.assertEqual((await s.scalars(select(RecoveryAction))).all(), [])
            node = await s.scalar(select(NodeRun).where(NodeRun.node_key == 'n1'))
            self.assertEqual(node.status, NodeRunStatus.failed)
            self.assertEqual((await s.get(ActionExecutionControl, 'exec-durable')).requests_used, 0)
        await self.switch(False)
        ticket = await self.retry()
        self.assertEqual(ticket['status'], 'queued')

    async def test_retry_endpoint_audits_denial_as_http_409(self):
        await self.exhausted_fixture()
        await self.switch(True)
        body = router.RetryNode(incident_id='source-incident', node_key='n1', rationale='try during stop',
                                idempotency_key='retry-during-stop-1')
        @asynccontextmanager
        async def project_sessions(_id):
            async with self.sessions() as s:
                yield s
        with patch('app.project_db.project_db_session_scope', project_sessions):
            with self.assertRaises(router.HTTPException) as ctx:
                await router.retry_node(7, 'exec-durable', body, None)
        self.assertEqual(ctx.exception.status_code, 409)
        async with self.sessions() as s:
            denied = await s.scalar(select(ActionRecord).where(ActionRecord.action_kind == 'recovery_denied'))
            self.assertIn('kill switch', denied.preconditions['reason'])


class KillSwitchEndpoints(KillSwitchBase):
    async def test_endpoint_uses_operator_identity_and_rationale(self):
        await self.enable()
        token = 'secret-session-token'
        expected = 'web_operator:' + hashlib.sha256(token.encode()).hexdigest()[:16]
        result = await router.kill_switch_change(7, router.KillSwitchChange(active=True, rationale='paid API runaway'), token)
        self.assertTrue(result['active'])
        self.assertEqual(result['changed_by'], expected)
        self.assertNotIn(token, str(result))
        local = await router.kill_switch_change(7, router.KillSwitchChange(active=False, rationale='cleared by owner'), None)
        self.assertEqual(local['changed_by'], 'local_web_operator')
        self.assertFalse(local['active'])

    async def test_endpoint_rejects_missing_rationale(self):
        with self.assertRaises(Exception):
            router.KillSwitchChange(active=True, rationale='x')
        with self.assertRaises(Exception):
            router.KillSwitchChange(active=True, rationale='valid', unexpected=1)

    async def test_read_only_endpoints_keep_working_while_stopped(self):
        await self.enable()
        await router.kill_switch_change(7, router.KillSwitchChange(active=True, rationale='read only check'), None)
        state = await router.kill_switch_state(7)
        self.assertTrue(state['active'])
        self.assertIn('watchdog', state)
        actions = await router.action_state(7, 'exec-durable')
        self.assertTrue(actions['enabled'])
        self.assertIn('kill_switch_on', [r['action_kind'] for r in actions['records']])
        listed = await router.list_observations(7)
        self.assertEqual(listed[0]['execution_id'], 'exec-durable')
        diagnosis = await router.diagnoses(7, 'exec-durable')
        self.assertIn('incidents', diagnosis)
        observer = observation._observer
        self.assertEqual(await observer.consume('exec-durable', 'master', observer.master), 0)


class KillSwitchDecisionLoop(KillSwitchBase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.service = importlib.import_module('app.services.decision_v1')
        @asynccontextmanager
        async def project_sessions(_id):
            async with self.sessions() as s:
                yield s
                await s.commit()
        patcher = patch('app.project_db.project_db_session_scope', project_sessions)
        patcher.start()
        self.addCleanup(patcher.stop)
        await self.exhausted_fixture()
        self.loop = self.service.DecisionSupervisor(observation._observer)
        await self.loop.configure(project_id=7, execution_id='exec-durable', enabled=True,
            initiator='synthetic-operator', rationale='kill switch decision fixture')

    async def records(self):
        async with self.sessions() as s:
            return (await s.scalars(select(self.service.DecisionRecord))).all()

    async def test_tick_opens_no_decision_and_makes_no_model_call_while_stopped(self):
        await self.switch(True)
        with patch('app.services.gpt_api.chat', side_effect=AssertionError('no model call under emergency stop')) as chat:
            await self.loop.tick()
        self.assertEqual(await self.records(), [])
        self.assertEqual(chat.call_count, 0)

    async def test_step_does_not_claim_or_spend_a_model_step_while_stopped(self):
        record = await self.loop.begin(project_id=7, execution_id='exec-durable', incident_id='source-incident')
        await self.switch(True)
        with patch('app.services.gpt_api.chat', side_effect=AssertionError('no model call under emergency stop')):
            await self.loop.step(record['decision_id'])
            await self.loop.tick()
        row = (await self.records())[0]
        self.assertEqual((row.status, row.model_steps, row.lease_token), ('ready', 0, None))
        async with self.sessions() as s:
            self.assertEqual((await s.get(ActionExecutionControl, 'exec-durable')).requests_used, 0)
            self.assertFalse((await s.get(ActionExecutionControl, 'exec-durable')).held,
                             'the stop must not leave a held flag behind')

    async def test_decision_resumes_after_switch_off(self):
        await self.switch(True)
        await self.loop.tick()
        self.assertEqual(await self.records(), [])
        await self.switch(False)
        with patch.object(self.service.DecisionSupervisor, 'step', new=AsyncMock()) as step:
            await self.loop.tick()
        self.assertEqual(len(await self.records()), 1)
        self.assertEqual(step.await_count, 1)


if __name__ == '__main__':
    unittest.main()
