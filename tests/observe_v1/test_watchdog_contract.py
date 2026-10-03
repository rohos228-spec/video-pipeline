"""Watchdog: stuck request / idle execution are marked, never retried; decision steps are isolated."""
import asyncio
import importlib
import unittest
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

import test_actions_v1_contract as actions_tests
from app.models import NodeRun, NodeRunStatus
from app.services import kill_switch, observation
from app.services.actions_recovery import RecoveryAction
from app.services.actions_v1 import ActionExecutionControl, ActionRecord
from app.services.diagnosis import Incident
from app.services.watchdog import Watchdog, get_watchdog
from app.settings import settings


class WatchdogBase(actions_tests.DurableActions):
    async def asyncSetUp(self):
        kill_switch.reset_cache()
        await super().asyncSetUp()
        self.t0 = datetime(2026, 10, 3, 12, 0, 0)
        for name, value in (('watchdog_enabled', True), ('watchdog_interval_s', 30.0),
                            ('watchdog_model_request_timeout_s', 900.0), ('watchdog_execution_idle_s', 1800.0),
                            ('watchdog_kill_switch_on_stall', False)):
            patcher = patch.object(settings, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.dog = Watchdog(observation._observer)

    async def asyncTearDown(self):
        kill_switch.reset_cache()
        await super().asyncTearDown()

    async def event(self, kind, *, at, request_id=None, attempt='att-1', node='n1', payload=None):
        async with self.sessions() as s:
            event = observation.ObservationEvent(event_id=str(uuid.uuid4()), execution_id='exec-durable',
                project_id=7, workflow_run_id=11, node_key=node, attempt_id=attempt, request_id=request_id,
                db_scope='master', kind=kind, payload=payload or {}, created_at=at)
            s.add(event)
            await s.commit()
            return event.event_id

    async def stalled(self):
        async with self.sessions() as s:
            return (await s.scalars(select(Incident).where(Incident.type == 'stalled'))).all()

    async def set_node(self, status, started_at=None):
        async with self.sessions() as s:
            node = await s.scalar(select(NodeRun).where(NodeRun.node_key == 'n1'))
            node.status = status
            node.started_at = started_at
            await s.commit()

    async def assert_nothing_dispatched(self):
        async with self.sessions() as s:
            self.assertEqual((await s.scalars(select(RecoveryAction))).all(), [])
            control = await s.get(ActionExecutionControl, 'exec-durable')
            if control:
                self.assertEqual(control.requests_used, 0)
                self.assertFalse(control.held, 'the watchdog marks; it never holds or resumes')


class WatchdogDetection(WatchdogBase):
    async def test_unfinished_model_request_older_than_timeout_is_marked_once_without_retry(self):
        await self.event('node_start', at=self.t0)
        await self.event('model_start', at=self.t0, request_id='req-stuck')
        with patch('app.services.gpt_api.chat', side_effect=AssertionError('watchdog must never call a model')):
            self.assertEqual(await self.dog.scan(now=self.t0 + timedelta(seconds=899)), [])
            created = await self.dog.scan(now=self.t0 + timedelta(seconds=901))
            self.assertEqual(await self.dog.scan(now=self.t0 + timedelta(seconds=5000)), [])
        self.assertEqual([c['stall']['kind'] for c in created], ['model_request'])
        (incident,) = await self.stalled()
        self.assertEqual((incident.status, incident.node_key, incident.attempt_id), ('human_review', 'n1', 'att-1'))
        details = incident.details
        self.assertTrue(details['blocking'] and details['currently_relevant'])
        self.assertEqual(details['request_ids'], ['req-stuck'])
        self.assertFalse(details['recommendation']['automatic_action'])
        self.assertEqual(details['human_review_summary']['status_label'], 'REQUIRES_HUMAN_INTERVENTION')
        self.assertEqual(details['human_review_summary']['incident_type'], 'stalled')
        self.assertIn('stalled', details['human_review_summary_text'])
        async with self.sessions() as s:
            record = await s.scalar(select(ActionRecord).where(ActionRecord.action_kind == 'watchdog_stalled'))
            self.assertEqual((record.initiator, record.status), ('watchdog', 'human_review'))
            self.assertFalse(record.preconditions['auto_retry'])
        await self.assert_nothing_dispatched()

    async def test_finished_or_fresh_or_diagnosed_requests_are_not_marked(self):
        await self.event('model_start', at=self.t0, request_id='req-ok')
        await self.event('model_end', at=self.t0 + timedelta(seconds=5), request_id='req-ok')
        await self.event('model_start', at=self.t0, request_id='req-err')
        await self.event('model_error', at=self.t0 + timedelta(seconds=5), request_id='req-err')
        # Ended attempt: diagnosis already derives uncertain_request for it.
        await self.event('model_start', at=self.t0, request_id='req-ended', attempt='att-2')
        await self.event('node_error', at=self.t0 + timedelta(seconds=7), attempt='att-2')
        await self.event('model_start', at=self.t0 + timedelta(seconds=2000), request_id='req-fresh', attempt='att-3')
        self.assertEqual(await self.dog.scan(now=self.t0 + timedelta(seconds=2100)), [])
        self.assertEqual(await self.stalled(), [])

    async def test_late_model_end_clears_the_mark_without_any_action(self):
        await self.event('model_start', at=self.t0, request_id='req-late')
        await self.dog.scan(now=self.t0 + timedelta(seconds=1000))
        await self.event('model_end', at=self.t0 + timedelta(seconds=1100), request_id='req-late')
        await self.dog.scan(now=self.t0 + timedelta(seconds=1200))
        (incident,) = await self.stalled()
        self.assertEqual(incident.status, 'resolved_confirmed')
        self.assertFalse(incident.details['blocking'])
        self.assertFalse(incident.details['resolution']['quality_verified'])
        await self.assert_nothing_dispatched()

    async def test_running_node_with_silent_journal_is_marked_idle(self):
        await self.set_node(NodeRunStatus.running, started_at=self.t0)
        await self.event('node_start', at=self.t0)
        await self.event('runtime_check', at=self.t0 + timedelta(seconds=60))
        self.assertEqual(await self.dog.scan(now=self.t0 + timedelta(seconds=60 + 1700)), [])
        created = await self.dog.scan(now=self.t0 + timedelta(seconds=60 + 1900))
        self.assertEqual([c['stall']['kind'] for c in created], ['execution_idle'])
        (incident,) = await self.stalled()
        self.assertEqual((incident.node_key, incident.attempt_id), ('n1', 'att-1'))
        self.assertEqual(incident.details['stall']['running_nodes'], ['n1'])
        self.assertEqual(await self.dog.scan(now=self.t0 + timedelta(seconds=9000)), [])  # same period, no duplicate
        await self.assert_nothing_dispatched()

    async def test_running_node_without_any_event_uses_its_start_time(self):
        await self.set_node(NodeRunStatus.running, started_at=self.t0)
        created = await self.dog.scan(now=self.t0 + timedelta(seconds=1801))
        self.assertEqual([c['stall']['kind'] for c in created], ['execution_idle'])

    async def test_idle_not_marked_without_running_node_and_resolves_on_activity(self):
        await self.event('node_start', at=self.t0)
        self.assertEqual(await self.dog.scan(now=self.t0 + timedelta(seconds=99999)), [])  # nothing running
        await self.set_node(NodeRunStatus.running, started_at=self.t0)
        await self.dog.scan(now=self.t0 + timedelta(seconds=2000))
        self.assertEqual(len(await self.stalled()), 1)
        await self.event('runtime_check', at=self.t0 + timedelta(seconds=2100))
        await self.dog.scan(now=self.t0 + timedelta(seconds=2200))
        (incident,) = await self.stalled()
        self.assertEqual(incident.details['resolution']['reason'], 'journal_activity_resumed')

    async def test_request_mark_suppresses_duplicate_idle_mark(self):
        await self.set_node(NodeRunStatus.running, started_at=self.t0)
        await self.event('model_start', at=self.t0, request_id='req-stuck')
        await self.dog.scan(now=self.t0 + timedelta(seconds=950))
        await self.dog.scan(now=self.t0 + timedelta(seconds=5000))
        self.assertEqual([i.details['stall']['kind'] for i in await self.stalled()], ['model_request'])

    async def test_stalled_incident_blocks_recovery_and_decision_ownership(self):
        await self.exhausted_fixture()
        async with self.sessions() as s:
            s.add(Incident(incident_id='stalled-1', execution_id='exec-durable', project_id=7, node_key='n1',
                attempt_id='source-attempt', type='stalled', severity='error', status='human_review',
                details={'currently_relevant': True, 'blocking': True, 'stall': {'kind': 'model_request'}}))
            await s.commit()
        with self.assertRaisesRegex(ValueError, 'blocker'):
            await self.retry()
        async with self.sessions() as s:
            self.assertEqual((await s.scalars(select(RecoveryAction))).all(), [])

    async def test_one_broken_execution_does_not_hide_the_others(self):
        await self.event('model_start', at=self.t0, request_id='req-stuck')
        real = Watchdog._scan_scope
        calls = []
        async def flaky(self_, execution_id, now):
            calls.append(execution_id)
            if execution_id == 'exec-broken':
                raise RuntimeError('boom')
            return await real(self_, execution_id, now)
        async with self.sessions() as s:
            s.add(observation.ExecutionControl(execution_id='exec-broken', project_id=7, workflow_run_id=11,
                node_keys=['n1'], graph_version=self.graph, mode='observe', observer_state='active',
                created_at=datetime.utcnow(), cursors={}, gaps=[]))
            await s.commit()
        with patch.object(Watchdog, '_scan_scope', flaky):
            created = await self.dog.scan(now=self.t0 + timedelta(seconds=1000))
        self.assertEqual(len(created), 1)
        self.assertEqual(self.dog.failures, {'exec-broken': 'RuntimeError'})

    async def test_stopped_observation_is_not_scanned(self):
        await self.event('model_start', at=self.t0, request_id='req-stuck')
        async with self.sessions() as s:
            (await s.get(observation.ExecutionControl, 'exec-durable')).observer_state = 'stopped'
            await s.commit()
        self.assertEqual(await self.dog.scan(now=self.t0 + timedelta(seconds=1000)), [])


class WatchdogKillSwitchTrigger(WatchdogBase):
    async def test_default_off_never_triggers_the_emergency_stop(self):
        await self.event('model_start', at=self.t0, request_id='req-stuck')
        await self.dog.scan(now=self.t0 + timedelta(seconds=1000))
        self.assertFalse((await kill_switch.status(self.sessions))['active'])

    async def test_configured_trigger_enables_the_stop_with_watchdog_identity(self):
        await self.event('model_start', at=self.t0, request_id='req-stuck')
        with patch.object(settings, 'watchdog_kill_switch_on_stall', True):
            await self.dog.scan(now=self.t0 + timedelta(seconds=1000))
        status = await kill_switch.status(self.sessions)
        self.assertTrue(status['active'])
        self.assertEqual((status['changed_by'], status['source']), ('watchdog', 'watchdog'))
        self.assertIn('model_request', status['rationale'])
        await self.assert_nothing_dispatched()


class WatchdogScheduling(WatchdogBase):
    async def test_tick_is_rate_limited_and_can_be_disabled(self):
        with patch.object(Watchdog, 'scan', new=AsyncMock(return_value=[])) as scan:
            await self.dog.tick()
            await self.dog.tick()
            self.assertEqual(scan.await_count, 1)
            await self.dog.tick(force=True)
            self.assertEqual(scan.await_count, 2)
            with patch.object(settings, 'watchdog_enabled', False):
                await self.dog.tick(force=True)
            self.assertEqual(scan.await_count, 2)

    async def test_get_watchdog_is_cached_per_observer(self):
        self.assertIs(get_watchdog(observation._observer), get_watchdog(observation._observer))


class DecisionIsolation(WatchdogBase):
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
        self.loop = self.service.DecisionSupervisor(observation._observer, background=True)
        await self.loop.configure(project_id=7, execution_id='exec-durable', enabled=True,
            initiator='synthetic-operator', rationale='decision isolation fixture')

    async def status(self):
        async with self.sessions() as s:
            return (await s.scalars(select(self.service.DecisionRecord))).one()

    async def test_slow_decision_step_does_not_block_the_shared_tick(self):
        started = asyncio.Event()
        release = asyncio.Event()
        async def slow_step(self_, decision_id):
            started.set()
            await release.wait()
        record = await self.loop.begin(project_id=7, execution_id='exec-durable', incident_id='source-incident')
        with patch.object(self.service.DecisionSupervisor, 'step', slow_step), \
                patch.object(self.service.DecisionSupervisor, 'drain', self.service.DecisionSupervisor.drain):
            # A blocking tick would wait on `release` forever and trip the 20 s guard.
            await asyncio.wait_for(self.loop.tick(), 20)
            self.assertFalse(release.is_set())
            await asyncio.wait_for(started.wait(), 5)
            # The loop keeps cycling: further ticks neither block nor start a second step.
            await self.loop.tick()
            await self.loop.tick()
            self.assertEqual(len(self.loop._step_tasks), 1)
            # ... and the other loop duties (journal delivery, diagnosis) run meanwhile.
            observer = observation._observer
            await observer.catch_up()
            from app.services.diagnosis import get_diagnoser
            await get_diagnoser(observer).catch_up()
            release.set()
            await self.loop.drain()
        self.assertEqual(self.loop._step_tasks, {})
        self.assertEqual(record['decision_id'], (await self.status()).decision_id)

    async def test_step_exceeding_its_timeout_is_cancelled_and_escalated_without_repost(self):
        async def hung_step(self_, decision_id):
            await asyncio.sleep(3600)
        record = await self.loop.begin(project_id=7, execution_id='exec-durable', incident_id='source-incident')
        with patch.object(self.service.DecisionSupervisor, 'step', hung_step), \
                patch.object(self.service.DecisionSupervisor, '_step_timeout', lambda self_: 0.2), \
                patch('app.services.gpt_api.chat', side_effect=AssertionError('no re-POST')):
            await self.loop.tick()
            await asyncio.wait_for(self.loop.drain(), 5)
        row = await self.status()
        self.assertEqual(row.decision_id, record['decision_id'])
        self.assertEqual(row.status, 'human_review')
        self.assertIn('exceeded', row.policy_result['reason'])
        self.assertIn('no repeat POST', row.policy_result['reason'])
        self.assertEqual(self.loop._step_tasks, {})
        async with self.sessions() as s:
            self.assertEqual((await s.scalars(select(RecoveryAction))).all(), [])

    async def test_inline_mode_is_unchanged_for_direct_construction(self):
        inline = self.service.DecisionSupervisor(observation._observer)
        self.assertFalse(inline.background)
        step = AsyncMock()
        await inline.begin(project_id=7, execution_id='exec-durable', incident_id='source-incident')
        with patch.object(self.service.DecisionSupervisor, 'step', step):
            await inline.tick()
        self.assertEqual(step.await_count, 1)  # awaited inline, no task
        self.assertEqual(inline._step_tasks, {})

    async def test_production_decider_is_background_and_runs_the_watchdog_hook(self):
        decider = self.service.get_decider(observation._observer)
        self.assertTrue(decider.background)
        with patch.object(Watchdog, 'tick', new=AsyncMock(return_value=[])) as tick:
            await decider.tick()
        self.assertEqual(tick.await_count, 1)
        inline = self.service.DecisionSupervisor(observation._observer)
        with patch.object(Watchdog, 'tick', new=AsyncMock(return_value=[])) as tick:
            await inline.tick()
        self.assertEqual(tick.await_count, 0)

    async def test_watchdog_runs_even_when_the_emergency_stop_is_on(self):
        await kill_switch.set_state(self.sessions, active=True, actor='op', rationale='stop for watchdog check')
        decider = self.service.get_decider(observation._observer)
        with patch.object(Watchdog, 'tick', new=AsyncMock(return_value=[])) as tick:
            await decider.tick()
        self.assertEqual(tick.await_count, 1)
        self.assertEqual(await self.session_records(), [])

    async def session_records(self):
        async with self.sessions() as s:
            return (await s.scalars(select(self.service.DecisionRecord))).all()

    async def test_a_failing_watchdog_never_breaks_the_decision_tick(self):
        decider = self.service.get_decider(observation._observer)
        with patch.object(Watchdog, 'tick', new=AsyncMock(side_effect=RuntimeError('boom'))):
            await decider.tick()  # must not raise


if __name__ == '__main__':
    unittest.main()
