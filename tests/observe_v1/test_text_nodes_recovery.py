"""recover_node / profile / reconcile preconditions for the native text nodes.

Seeds the state a real exhausted native plan/script run leaves behind: a typed invalid_json
incident, NO inner `json_retry` fact (native text executors have none), failed NodeRun,
step_failure total_fails at the cap, abandoned. Asserts that the recovery either is admitted
or is rejected for the intended reason, node type by node type. Every existing guard is
re-asserted on a text node. No provider, no worker.
"""
import unittest
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from app.models import HITLDecision, HITLKind, HITLRequest, NodeRun, NodeRunStatus, Project, WorkflowRun
from app.services import observation
from app.services.actions_recovery import RecoveryAction, reconcile
from app.services.actions_v1 import ActionExecutionControl, get_permission
from app.services.diagnosis import DiagnosisState, Incident
from app.services.observation import ObservationEvent, digest
from app.services.profiles_v1 import ProfilesSupervisor
import test_actions_v1_contract as base_tests  # module import: do not re-collect its classes here


class TextNodeRecovery(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = base_tests.DurableActions.asyncSetUp
    asyncTearDown = base_tests.DurableActions.asyncTearDown
    exhausted_fixture = base_tests.DurableActions.exhausted_fixture
    retry = base_tests.DurableActions.retry
    assert_no_dispatch = base_tests.DurableActions.assert_no_dispatch
    mixed_recovery_fixture = base_tests.RecoveryCapabilityAdmission.mixed_recovery_fixture

    async def approved_profile_fixture(self):
        from app.services.profiles_state import ProfileOverlay
        from app.services.profiles_v1 import CATALOG, catalog_hash, effective_body
        from app.services.actions_recovery import input_revision
        async with self.sessions() as session:
            project = await session.get(Project, 7)
            revision = await input_revision(session, project, 'n1')
            candidate = dict(CATALOG['synthetic-json-v1'])
            session.add(ProfileOverlay(overlay_id='text-profile', execution_id='exec-durable', project_id=7,
                node_key='n1', source_attempt_id='source-attempt', profile_id='synthetic-json-v1',
                base_version=revision, graph_version=self.graph, catalog_hash=catalog_hash(),
                candidate_hash=digest(candidate), candidate=candidate, source_body_hash=digest(self.SOURCE_BODY),
                expected_body_hash=digest(effective_body(self.SOURCE_BODY, candidate)), initiator='synthetic',
                rationale='text node profile test', status='approved', history=[]))
            await session.commit()
        return 'text-profile'

    EXECUTION_UUID = '00000000-0000-0000-0000-000000000007'
    SOURCE_BODY = {'model': 'gpt-5-6-sol', 'stream': True, 'store': False, 'reasoning': {'effort': 'high'},
                   'input': [{'role': 'user', 'content': 'Attachments: 1. prompt_plan_20261001_205923.txt\noriginal facts'}]}

    def hermetic_runtime(self):
        """Explicit synthetic loopback runtime config: independent of whatever env/settings other
        modules of a combined pytest run left behind."""
        from app.services import decision_config
        cfg = decision_config.validate_config({'version': 1, 'mode': 'synthetic', 'online_enabled': True,
            'endpoint': 'http://127.0.0.1:18766/codex/v1/responses',
            'decision': {'catalog_id': 'gpt-kie', 'model_id': 'gpt-5-6-sol', 'reasoning_effort': 'low',
                         'max_output_tokens': 1024, 'timeout_s': 30},
            'profiles': {}, 'allowed_actions': sorted(decision_config.ALL_ACTIONS)})
        # Patch the settings object the application resolves NOW (a combined run can leave a
        # different instance behind than the one conftest's autouse fixture patched).
        from app.settings import settings as live_settings
        for patcher in (patch.object(decision_config, 'runtime_config', return_value=cfg),
                        patch.object(live_settings, 'gpt_api_key', 'synthetic-observe-test-key'),
                        patch.object(live_settings, 'gpt_base_url', 'http://127.0.0.1:18766')):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def text_fixture(self, node_type, *, inner_json_retry=False, incident_type='invalid_json'):
        self.hermetic_runtime()
        await self.mixed_recovery_fixture(node_type)
        async with self.sessions() as s:
            # The recorded source request body (what recover_node compares the recovery request to).
            source = await s.scalar(select(ObservationEvent).where(ObservationEvent.kind == 'model_start'))
            source.payload = {'input_revision': digest(self.SOURCE_BODY),
                              'body': observation._observer.save_body(self.EXECUTION_UUID, self.SOURCE_BODY)}
            if not inner_json_retry:
                for event in (await s.scalars(select(ObservationEvent).where(ObservationEvent.kind == 'json_retry'))).all():
                    await s.delete(event)
            parse = await s.scalar(select(ObservationEvent).where(ObservationEvent.kind == 'parse_result'))
            parse.payload = {'payload_required': incident_type in {'invalid_json', 'missing_payload'},
                             'parsed': False, 'syntax_valid': False, 'payload_present': False}
            (await s.get(Incident, 'source-incident')).type = incident_type
            await s.commit()

    # ---- admission by node type -------------------------------------------------------
    async def test_plan_and_script_exhausted_invalid_json_without_inner_retry_is_admitted(self):
        for node_type in ('plan', 'script'):
            with self.subTest(node_type=node_type):
                await self.asyncTearDown()
                await self.asyncSetUp()
                await self.text_fixture(node_type)
                ticket = await self.retry()
                self.assertEqual(ticket['status'], 'queued')
                self.assertEqual(ticket['preconditions']['effective_node_type'], node_type)
                self.assertEqual(ticket['preconditions']['action_type'], 'retry_same')
                self.assertIn('retry_same', ticket['preconditions']['capability_actions'])
                self.assertTrue(ticket['preconditions']['prior_attempt_terminal'])
                self.assertTrue(ticket['preconditions']['no_partial_apply'])

    async def test_plan_and_script_profile_recovery_is_admitted_through_reservation(self):
        for node_type in ('plan', 'script'):
            with self.subTest(node_type=node_type):
                await self.asyncTearDown()
                await self.asyncSetUp()
                await self.text_fixture(node_type)
                overlay_id = await self.approved_profile_fixture()
                ticket = await self.retry(profile_overlay_id=overlay_id)
                self.assertEqual(ticket['preconditions']['action_type'], 'retry_profile')
                self.assertEqual(ticket['preconditions']['profile_overlay_id'], overlay_id)

    async def test_split_and_image_prompts_are_still_denied_by_capability(self):
        for node_type in ('split', 'image_prompts'):
            for profile in (False, True):
                with self.subTest(node_type=node_type, profile=profile):
                    await self.asyncTearDown()
                    await self.asyncSetUp()
                    await self.text_fixture(node_type)
                    overlay_id = await self.approved_profile_fixture() if profile else None
                    with self.assertRaisesRegex(ValueError, 'denied by node capability'):
                        await self.retry(profile_overlay_id=overlay_id)
                    await self.assert_no_dispatch()

    async def test_split_style_contract_rejected_incident_is_refused_for_the_incident_reason(self):
        """What split/image_prompts failures look like (payload_required=False -> contract_rejected)."""
        await self.text_fixture('plan', incident_type='contract_rejected')
        with self.assertRaisesRegex(ValueError, 'current mandatory JSON/payload incident required'):
            await self.retry()
        await self.assert_no_dispatch()

    async def test_excel_gpt_still_needs_its_inner_json_retry_fact(self):
        await self.text_fixture('excel_gpt')  # no json_retry fact
        with self.assertRaisesRegex(ValueError, 'native JSON retries not exhausted'):
            await self.retry()
        await self.assert_no_dispatch()

    async def test_excel_gpt_with_inner_json_retry_is_admitted_as_before(self):
        await self.text_fixture('excel_gpt', inner_json_retry=True)
        self.assertEqual((await self.retry())['preconditions']['effective_node_type'], 'excel_gpt')

    # ---- first-request identity: only attachment file-name stamps are masked --------------
    async def first_request_check(self, node_type, body, *, profile=False):
        from types import SimpleNamespace
        from app.services.actions_recovery import check_body
        await self.text_fixture(node_type)
        ticket = await self.retry(profile_overlay_id=await self.approved_profile_fixture() if profile else None)
        ctx = SimpleNamespace(observer=observation._observer, attempt_id=ticket['new_attempt_id'], last_request_id=None,
            scope=SimpleNamespace(execution_id='exec-durable', observer_state='active', gaps=[]))
        if profile:
            from app.services.profiles_v1 import CATALOG, effective_body
            body = effective_body(body, dict(CATALOG['synthetic-json-v1']))
        await check_body(ctx, digest(body), body=body)
        return ticket

    def with_stamp(self, stamp, tail=''):
        content = self.SOURCE_BODY['input'][0]['content'].replace('20261001_205923', stamp) + tail
        return {**self.SOURCE_BODY, 'input': [{'role': 'user', 'content': content}]}

    async def test_text_recovery_accepts_the_same_body_with_new_attachment_stamps(self):
        for node_type in ('plan', 'script'):
            for profile in (False, True):
                with self.subTest(node_type=node_type, profile=profile):
                    await self.asyncTearDown()
                    await self.asyncSetUp()
                    ticket = await self.first_request_check(node_type, self.with_stamp('20261001_205929'), profile=profile)
                    self.assertEqual(ticket['preconditions']['first_body_normalization'], 'attachment_timestamps')

    async def test_text_recovery_still_blocks_any_real_body_change(self):
        for profile in (False, True):
            with self.subTest(profile=profile):
                await self.asyncTearDown()
                await self.asyncSetUp()
                with self.assertRaisesRegex(RuntimeError, 'before transport'):
                    await self.first_request_check('plan', self.with_stamp('20261001_205929', tail=' + new instruction'),
                                                   profile=profile)

    async def test_excel_gpt_keeps_the_exact_first_body_comparison(self):
        await self.text_fixture('excel_gpt', inner_json_retry=True)
        ticket = await self.retry()
        self.assertNotIn('first_body_normalization', ticket['preconditions'])
        self.assertEqual(ticket['preconditions']['first_body_hash'], digest(self.SOURCE_BODY))

    async def test_unavailable_source_body_denies_text_recovery_without_dispatch(self):
        await self.text_fixture('plan')
        async with self.sessions() as s:
            source = await s.scalar(select(ObservationEvent).where(ObservationEvent.kind == 'model_start'))
            source.payload = {'input_revision': digest(self.SOURCE_BODY)}
            await s.commit()
        with self.assertRaisesRegex(ValueError, 'source request body unavailable'):
            await self.retry()
        await self.assert_no_dispatch()

    # ---- every guard still applies to a text node -------------------------------------
    async def test_text_node_native_retries_must_be_exhausted(self):
        await self.text_fixture('plan')
        async with self.sessions() as s:
            p = await s.get(Project, 7)
            p.meta = {**p.meta, 'step_failure': {**p.meta['step_failure'], 'total_fails': {'enriching_1': 8}}}
            await s.commit()
        with self.assertRaisesRegex(ValueError, 'native runtime retries must be exhausted'):
            await self.retry()
        await self.assert_no_dispatch()

    async def test_text_node_user_stop_budget_graph_hold_and_hitl_guards(self):
        cases = {
            'user_stop': ('user_stop', lambda s: self._set_meta(s, {'user_stop': True})),
            'budget': ('budget exhausted', self._exhaust_budget),
            'graph': ('graph revision', self._change_graph),
            'held': ('disabled or held', self._hold),
            'hitl': ('HITL', self._pending_hitl_node),
        }
        for name, (pattern, mutate) in cases.items():
            with self.subTest(guard=name):
                await self.asyncTearDown()
                await self.asyncSetUp()
                await self.text_fixture('plan')
                async with self.sessions() as s:
                    await mutate(s)
                    await s.commit()
                with self.assertRaisesRegex(ValueError, pattern):
                    await self.retry()
                await self.assert_no_dispatch()

    async def test_single_recovery_per_node_even_with_a_new_idempotency_key(self):
        await self.text_fixture('script')
        await self.retry()
        with self.assertRaisesRegex(ValueError, 'single recovery already consumed'):
            await self.retry(idempotency_key='second-retry-0002')

    async def test_text_node_input_change_between_failure_and_recovery_is_refused(self):
        await self.text_fixture('script')
        async with self.sessions() as s:
            p = await s.get(Project, 7)
            p.general_plan = 'plan changed after the failed attempt'
            await s.commit()
        with self.assertRaisesRegex(ValueError, 'input/config revision changed'):
            await self.retry()
        await self.assert_no_dispatch()

    async def test_text_node_inner_apply_evidence_blocks_recovery(self):
        await self.text_fixture('plan')
        async with self.sessions() as s:
            s.add(ObservationEvent(id=7, event_id=str(uuid.uuid4()), execution_id='exec-durable', project_id=7,
                workflow_run_id=11, node_key='n1', attempt_id='source-attempt', db_scope='project',
                kind='data_applied', payload={}, created_at=datetime.utcnow()))
            (await s.get(DiagnosisState, 'exec-durable')).cursor = 7
            await s.commit()
        with self.assertRaisesRegex(ValueError, 'partial apply'):
            await self.retry()

    async def _set_meta(self, s, extra):
        p = await s.get(Project, 7)
        p.meta = {**p.meta, **extra}

    async def _exhaust_budget(self, s):
        c = await s.get(ActionExecutionControl, 'exec-durable')
        c.requests_used = c.request_limit

    async def _change_graph(self, s):
        (await s.get(WorkflowRun, 11)).edges_snapshot = [{'source': 'n1', 'target': 'n2'}]

    async def _hold(self, s):
        (await s.get(ActionExecutionControl, 'exec-durable')).held = True

    async def _pending_hitl_node(self, s):
        (await s.scalar(select(NodeRun).where(NodeRun.node_key == 'n2'))).status = NodeRunStatus.waiting_hitl

    # ---- profile eligibility is capability data ----------------------------------------
    async def test_decision_context_lists_profiles_only_for_profile_compatible_nodes(self):
        # eligible_profiles is computed from the launch snapshot and gated by capability data;
        # the gate expression itself is asserted here so a node without the flag can never get one.
        from app.services import decision_v1
        import inspect
        source = inspect.getsource(decision_v1.DecisionSupervisor._context)
        self.assertIn('capability.profile_compatible', source)
        self.assertNotIn("result_kind=='structured'", source)

    async def test_profile_base_gate_by_capability_for_each_node_type(self):
        """ProfilesSupervisor._base: text nodes with the flag pass the capability gate; the others
        are refused with the capability reason (or fail later on a different, non-capability guard)."""
        expectations = {'plan': True, 'script': True, 'excel_gpt': True, 'split': False, 'image_prompts': False}
        for node_type, allowed in expectations.items():
            with self.subTest(node_type=node_type):
                await self.asyncTearDown()
                await self.asyncSetUp()
                await self.text_fixture(node_type)
                async with self.sessions() as s:
                    scope = await s.get(observation.ExecutionControl, 'exec-durable')
                    control = await s.get(ActionExecutionControl, 'exec-durable')
                    scope.node_keys = control.node_keys = ['n1', 'n2']
                    await s.commit()
                supervisor = ProfilesSupervisor(observation._observer)
                async with self.sessions() as s:
                    try:
                        with patch('app.project_db.project_db_session_scope', lambda _id: self.sessions()):
                            await supervisor._base(s, 7, 'exec-durable', 'n1')
                        reason = None
                    except ValueError as exc:
                        reason = str(exc)
                if allowed:
                    self.assertNotEqual(reason, 'incompatible accepted text node contract')
                else:
                    self.assertEqual(reason, 'incompatible accepted text node contract')

    # ---- verification at the HITL boundary --------------------------------------------
    async def verified_attempt(self, node_type, *, done_event='attempt'):
        await self.text_fixture(node_type, inner_json_retry=node_type == 'excel_gpt')
        ticket = await self.retry()
        async with self.sessions() as s:
            row = await s.get(RecoveryAction, ticket['action_id'])
            row.status = 'await_verify'
            row.result = {'worker_returned_at': '2020-01-01T00:00:00'}
            (await s.scalar(select(NodeRun).where(NodeRun.node_key == 'n1'))).status = NodeRunStatus.done
            facts = [('node_start', {}, None), ('model_start', {}, 'r'), ('model_end', {'complete': True}, 'r'),
                     ('parse_result', {'payload_required': True, 'parsed': True, 'syntax_valid': True,
                                       'payload_present': True, 'schema_valid': True}, 'r'),
                     ('data_applied', {'transactional': True, 'result': {'ok': True}}, 'r'),
                     ('node_change', {'transactional': True, 'changes': {'status': {'after': ['done']}}}, None),
                     ('node_end', {}, None)]
            done = ('node_change', {'transactional': True, 'changes': {'status': {'after': ['done']}}}, None)
            facts = [f for f in facts if f[0] != 'node_change' or done_event == 'attempt']
            for kind, payload, request_id in facts:
                s.add(ObservationEvent(event_id=uuid.uuid4().hex, execution_id='exec-durable', project_id=7,
                    workflow_run_id=11, node_key='n1', attempt_id=row.new_attempt_id, request_id=request_id,
                    db_scope='project', kind=kind, payload=payload, created_at=datetime.utcnow()))
            await s.flush()
            if done_event in {'boundary', 'other_start'}:
                if done_event == 'other_start':
                    s.add(ObservationEvent(event_id=uuid.uuid4().hex, execution_id='exec-durable', project_id=7,
                        workflow_run_id=11, node_key='n1', attempt_id='some-other-attempt', db_scope='project',
                        kind='node_start', payload={}, created_at=datetime.utcnow()))
                # the runtime's own commit of NodeRun done, after the executor returned: no attempt id
                s.add(ObservationEvent(event_id=uuid.uuid4().hex, execution_id='exec-durable', project_id=7,
                    workflow_run_id=11, node_key='n1', attempt_id=None, db_scope='project', kind=done[0],
                    payload=done[1], created_at=datetime.utcnow()))
            await s.commit()
        return ticket

    async def run_reconcile(self):
        @asynccontextmanager
        async def project_session(_id):
            async with self.sessions() as s:
                yield s

        async def no_sync(*args):
            pass
        continuation = AsyncMock()
        with patch('app.project_db.project_db_session_scope', project_session), \
             patch('app.project_db.pull_master_runtime_into_project', no_sync), \
             patch('app.project_db.push_runtime_to_master', no_sync), \
             patch('app.orchestrator.steps.enrich_xlsx._after_excel_gpt_done', continuation):
            await reconcile(observation._observer)
        return continuation

    async def test_verified_text_recovery_adds_no_continuation_and_never_touches_the_pending_hitl(self):
        for node_type in ('plan', 'script'):
            with self.subTest(node_type=node_type):
                await self.asyncTearDown()
                await self.asyncSetUp()
                ticket = await self.verified_attempt(node_type)
                async with self.sessions() as s:
                    s.add(HITLRequest(id=1, project_id=7, kind=HITLKind.approve_plan if node_type == 'plan'
                                      else HITLKind.approve_script, payload={}, decision=HITLDecision.pending))
                    await s.commit()
                continuation = await self.run_reconcile()
                continuation.assert_not_awaited()
                async with self.sessions() as s:
                    row = await s.get(RecoveryAction, ticket['action_id'])
                    self.assertEqual(row.status, 'verified', row.verification_result)
                    self.assertTrue(row.verification_result['verified'])
                    self.assertFalse(row.verification_result['semantic_quality_verified'])
                    self.assertIs(row.result['continuation_submitted'], False)
                    hitl = await s.get(HITLRequest, 1)
                    self.assertEqual(hitl.decision, HITLDecision.pending)
                    self.assertIsNone(hitl.decided_at)

    async def test_text_node_done_committed_after_the_executor_returned_is_the_hitl_boundary_proof(self):
        for node_type in ('plan', 'script'):
            with self.subTest(node_type=node_type):
                await self.asyncTearDown()
                await self.asyncSetUp()
                ticket = await self.verified_attempt(node_type, done_event='boundary')
                await self.run_reconcile()
                async with self.sessions() as s:
                    row = await s.get(RecoveryAction, ticket['action_id'])
                    self.assertEqual(row.status, 'verified', row.verification_result)
                    self.assertIn('native text node', row.verification_result['done_proof'])

    async def test_boundary_proof_is_not_accepted_without_a_committed_done_or_after_another_attempt(self):
        for node_type, done_event in (('plan', 'none'), ('plan', 'other_start')):
            with self.subTest(done_event=done_event):
                await self.asyncTearDown()
                await self.asyncSetUp()
                ticket = await self.verified_attempt(node_type, done_event=done_event)
                await self.run_reconcile()
                async with self.sessions() as s:
                    row = await s.get(RecoveryAction, ticket['action_id'])
                    self.assertEqual(row.status, 'human_review')
                    self.assertFalse(row.verification_result['transactional_done'])

    async def test_excel_gpt_does_not_get_the_boundary_proof(self):
        ticket = await self.verified_attempt('excel_gpt', done_event='boundary')
        await self.run_reconcile()
        async with self.sessions() as s:
            row = await s.get(RecoveryAction, ticket['action_id'])
            self.assertEqual(row.status, 'human_review')
            self.assertFalse(row.verification_result['transactional_done'])

    async def test_verified_excel_recovery_keeps_its_original_continuation(self):
        ticket = await self.verified_attempt('excel_gpt')
        continuation = await self.run_reconcile()
        continuation.assert_awaited_once()
        async with self.sessions() as s:
            self.assertEqual((await s.get(RecoveryAction, ticket['action_id'])).status, 'verified')

    async def test_text_recovery_without_applied_result_is_not_verified_and_holds_execution(self):
        ticket = await self.verified_attempt('plan')
        async with self.sessions() as s:
            for e in (await s.scalars(select(ObservationEvent).where(ObservationEvent.kind == 'data_applied'))).all():
                await s.delete(e)
            await s.commit()
        await self.run_reconcile()
        async with self.sessions() as s:
            row = await s.get(RecoveryAction, ticket['action_id'])
            self.assertEqual(row.status, 'human_review')
            self.assertFalse(row.verification_result['verified'])
            self.assertTrue((await s.get(ActionExecutionControl, 'exec-durable')).held)
        self.assertTrue(get_permission(7).held)


if __name__ == '__main__':
    unittest.main()
