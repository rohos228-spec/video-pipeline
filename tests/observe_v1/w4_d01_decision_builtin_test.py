"""W4 / D01: Decision in production mode with the built-in profile catalog (no ORCHESTRATOR_RUNTIME_CONFIG
hand-built profiles). Real Decision/Profiles/Actions code on the synthetic fixture database; the decision
model is a scripted stand-in. Class D. Nothing here ran live.
"""
import importlib
import json
from unittest.mock import patch

from sqlalchemy import select

import test_decision_v1 as base_tests
from app.services import observation
from app.services.actions_recovery import RecoveryAction
from app.services.managed_launch import ManagedLaunch

ACTIONS = ['read_events', 'read_contract', 'retry_same', 'retry_profile', 'human_review', 'wait', 'finish']


def production():
    return {'version': 1, 'mode': 'production', 'online_enabled': True,
            'endpoint': 'http://127.0.0.1:18766/codex/v1/responses',
            'decision': {'catalog_id': 'gpt-kie', 'model_id': 'gpt-5-6-sol', 'reasoning_effort': 'low',
                         'max_output_tokens': 1024, 'timeout_s': 30},
            'profiles': {}, 'allowed_actions': list(ACTIONS)}


class BuiltinCatalogDecision(base_tests.DecisionDurability):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.config = importlib.import_module('app.services.decision_config')
        self.profiles = importlib.import_module('app.services.profiles_v1')
        self.runtime = patch.object(self.config, 'runtime_config', return_value=production())
        self.runtime_mock = self.runtime.start()
        self.addCleanup(self.runtime.stop)

    async def record_source_body(self):
        """The source request body the profile approval re-reads (as test_profiles_v1 prepares it)."""
        async with self.sessions() as s:
            event = await s.scalar(select(observation.ObservationEvent).where(
                observation.ObservationEvent.kind == 'model_start'))
            body = {'model': 'gpt-5-6-sol', 'input': 'original resolved prompt', 'stream': True,
                    'reasoning': {'effort': 'high'}}
            event.payload = {**event.payload, 'input_revision': observation.digest(body),
                             'body': observation._observer.save_body('exec-durable', body)}
            await s.commit()

    async def launch(self, ids):
        catalog = self.profiles.active_catalog()
        async with self.sessions() as s:
            s.add(ManagedLaunch(launch_id='launch-builtin', execution_id='exec-durable', project_id=7,
                workflow_run_id=11, idempotency_key='launch-builtin-test', request_hash='test', entry_node='n1',
                status='submitted', result={}, policy={'allowed_actions': list(ACTIONS), 'profile_ids': ids,
                    'profile_catalog_hash': self.profiles.catalog_hash(), 'profiles': {k: catalog[k] for k in ids}}))
            await s.commit()

    def reply(self, action, arguments=None):
        from app.services.gpt_api import GptChatResult

        async def chat(**kw):
            ctx = json.loads(kw['prompt'])
            return GptChatResult(text=json.dumps({'action': action, 'arguments': arguments or {},
                'evidence_ids': ctx['evidence_ids'][:1], 'context_version': ctx['context_version'],
                'explanation': 'Native retries exhausted; recorded payload was rejected'}),
                model='synthetic', raw={'status': 'completed'})
        return chat

    async def test_status_reports_builtin_profiles_in_production(self):
        st = self.config.status()
        self.assertEqual(st['mode'], 'production')
        self.assertGreaterEqual(st['profile_count'], 3)
        self.assertEqual(st['profile_source'], 'builtin')

    async def test_production_decision_selects_retry_same_on_stand_in_model(self):
        await self.enable_decisions()
        record = await self.begin()
        context = await self.loop.context(record['decision_id'])
        self.assertIn('retry_same', context['allowed_actions'])
        with patch('app.services.gpt_api.chat', side_effect=self.reply('retry_same')) as chat:
            await self.loop.step(record['decision_id'])
            await self.service.DecisionSupervisor(observation._observer).tick()
            self.assertEqual(chat.call_count, 1)
        async with self.sessions() as s:
            row = await s.get(self.service.DecisionRecord, record['decision_id'])
            self.assertEqual(row.status, 'waiting_worker', row.policy_result)
            tickets = (await s.scalars(select(RecoveryAction))).all()
            self.assertEqual([t.preconditions['action_type'] for t in tickets], ['retry_same'])

    async def test_context_offers_builtin_profiles_only_when_launch_approved_them(self):
        await self.enable_decisions()
        record = await self.begin()
        context = await self.loop.context(record['decision_id'])
        self.assertEqual(context['eligible_profiles'], [])
        await self.launch(['kie-json-repair-v1', 'kie-json-repair-roomy-v1'])
        context = await self.loop.context(record['decision_id'])
        self.assertEqual(context['incident_type'], 'missing_payload')
        self.assertEqual([p['profile_id'] for p in context['eligible_profiles']],
                         ['kie-json-repair-v1', 'kie-json-repair-roomy-v1'])
        self.assertEqual(context['recommended_profile_id'], 'kie-json-repair-v1')
        self.assertTrue(all(p['candidate']['synthetic_only'] is False for p in context['eligible_profiles']))

    async def test_production_decision_selects_builtin_profile_with_one_ticket(self):
        await self.record_source_body()
        await self.enable_decisions()
        record = await self.begin()
        await self.launch(['kie-json-repair-roomy-v1'])
        with patch('app.services.gpt_api.chat',
                   side_effect=self.reply('retry_profile', {'profile_id': 'kie-json-repair-roomy-v1'})) as chat:
            await self.loop.step(record['decision_id'])
            await self.service.DecisionSupervisor(observation._observer).tick()
            self.assertEqual(chat.call_count, 1)
        async with self.sessions() as s:
            row = await s.get(self.service.DecisionRecord, record['decision_id'])
            self.assertEqual(row.status, 'waiting_worker', row.policy_result)
            tickets = (await s.scalars(select(RecoveryAction))).all()
            self.assertEqual([t.preconditions['action_type'] for t in tickets], ['retry_profile'])

    async def test_binding_hides_builtin_profiles_for_other_node_types_and_incidents(self):
        await self.enable_decisions()
        record = await self.begin()
        await self.launch(['kie-json-repair-v1'])
        from app.models import WorkflowRun
        async with self.sessions() as s:
            run = await s.get(WorkflowRun, 11)
            run.nodes_snapshot = [{**n, 'type': 'images'} if n['id'] == 'n1' else n for n in run.nodes_snapshot]
            await s.commit()
        async with self.sessions() as s:
            row = await s.get(self.service.DecisionRecord, record['decision_id'])
            self.assertEqual(await self.profiles.eligible_profiles(s, row, 'base'), [])


# The base classes' own tests are not repeated under the production config (they have their own runs).
for _name in dir(base_tests.DecisionDurability):
    if _name.startswith('test') and _name not in vars(BuiltinCatalogDecision):
        setattr(BuiltinCatalogDecision, _name, None)
