"""Profiles must be immutable, scoped and consume the existing recovery ticket."""
import importlib,json,unittest
from unittest.mock import patch
from sqlalchemy import select
from app.services import observation
from app.services.actions_v1 import ActionExecutionControl
from app.services.actions_recovery import RecoveryAction
from test_decision_v1 import DecisionDurability

class Profiles(DecisionDurability):
    async def asyncSetUp(self):
        self.profiles=importlib.import_module('app.services.profiles_v1')
        await super().asyncSetUp()
        async with self.sessions() as s:
            from app.models import WorkflowRun
            run=await s.get(WorkflowRun,11)
            run.nodes_snapshot=[{**n,'type':'excel_gpt'} if n['id']=='n1' else n for n in run.nodes_snapshot]
            self.graph=observation.digest({'nodes':run.nodes_snapshot,'edges':run.edges_snapshot})
            scope=await s.get(observation.ExecutionControl,'exec-durable');scope.graph_version=self.graph
            control=await s.get(ActionExecutionControl,'exec-durable');control.group_id='script_frames_qc';control.graph_version=self.graph
            event=await s.scalar(select(observation.ObservationEvent).where(observation.ObservationEvent.kind=='model_start'))
            self.source_body={'model':'gpt-5-6-sol','input':'original resolved prompt','stream':True,'reasoning':{'effort':'high'}}
            event.payload={**event.payload,'input_revision':observation.digest(self.source_body),
                'body':observation._observer.save_body('exec-durable',self.source_body)}
            await s.commit()
        self.store=self.profiles.ProfilesSupervisor(observation._observer)

    async def approve(self,**extra):
        state=await self.store.state(7,'exec-durable','n1')
        args=dict(project_id=7,execution_id='exec-durable',node_key='n1',profile_id='synthetic-json-v1',
            base_version=state['base_version'],catalog_hash=state['catalog_hash'],initiator='synthetic-operator',
            rationale='approve fixed synthetic profile for one next attempt')
        args.update(extra)
        return await self.store.approve(**args)

    async def test_default_no_active_overlay(self):
        state=await self.store.state(7,'exec-durable','n1')
        self.assertEqual(state['overlays'],[])
        self.assertTrue(state['synthetic_only'])

    async def test_approval_persists_version_and_does_not_change_project(self):
        overlay=await self.approve()
        other=self.profiles.ProfilesSupervisor(observation._observer)
        state=await other.state(7,'exec-durable','n1')
        self.assertEqual(state['overlays'][0]['overlay_id'],overlay['overlay_id'])
        self.assertEqual(state['overlays'][0]['status'],'approved')
        self.assertEqual(state['overlays'][0]['base_version'],state['base_version'])

    async def test_stale_base_denied(self):
        with self.assertRaisesRegex(ValueError,'stale'):await self.approve(base_version='old')

    async def test_unknown_profile_provider_and_parameters_not_accepted(self):
        for name in ('foreign-provider','synthetic-image-v1','../../prompt','unapproved'):
            with self.assertRaises(ValueError):await self.approve(profile_id=name)
        with self.assertRaises(ValueError):await self.approve(catalog_hash='tampered')

    async def test_foreign_scope_denied(self):
        with self.assertRaises(ValueError):await self.approve(project_id=8)
        with self.assertRaises(ValueError):await self.approve(node_key='outside')

    async def test_real_credentials_never_make_synthetic_catalog_production_approved(self):
        from app.settings import settings
        with patch.object(settings,'gpt_api_key','real-looking-credential'):
            self.assertEqual(self.profiles.active_catalog(),{})
            with self.assertRaises(ValueError):await self.approve()

    async def test_candidate_is_exact_fixed_transformation(self):
        overlay=await self.approve()
        candidate=self.profiles.effective_body(self.source_body,overlay['candidate'])
        self.assertEqual(candidate['model'],'gpt-synthetic-alt')
        self.assertEqual(candidate['reasoning'],{'effort':'medium'})
        self.assertEqual(candidate['max_output_tokens'],2048)
        self.assertTrue(candidate['input'].startswith(self.source_body['input']))
        self.assertNotEqual(observation.digest(candidate),observation.digest(self.source_body))
        self.assertEqual(overlay['expected_body_hash'],observation.digest(candidate))

    async def test_original_responses_message_input_is_supported_without_losing_roles(self):
        body={**self.source_body,'input':[{'role':'system','content':'contract'},{'role':'user','content':'source'}]}
        candidate=self.profiles.effective_body(body,self.profiles.CATALOG['synthetic-json-v1'])
        self.assertEqual(candidate['input'][0],body['input'][0])
        self.assertEqual(candidate['input'][1]['role'],'user')
        self.assertTrue(candidate['input'][1]['content'].startswith('source'))
        self.assertEqual(body['input'][1]['content'],'source')

    async def test_profile_recovery_uses_one_original_ticket_and_restart_no_duplicate(self):
        overlay=await self.approve()
        args=dict(project_id=7,execution_id='exec-durable',incident_id='source-incident',node_key='n1',
            overlay_id=overlay['overlay_id'],idempotency_key='profile-recovery-test',initiator='operator',rationale='synthetic')
        first=await self.store.recover(**args)
        again=await self.profiles.ProfilesSupervisor(observation._observer).recover(**args)
        self.assertEqual(first['action_id'],again['action_id'])
        self.assertEqual(first['preconditions']['action_type'],'retry_profile')
        self.assertEqual(first['preconditions']['first_body_hash'],overlay['expected_body_hash'])
        async with self.sessions() as s:self.assertEqual(len((await s.scalars(select(RecoveryAction))).all()),1)

    async def test_retry_same_retains_original_body_guard(self):
        await self.approve();ticket=await self.retry()
        self.assertEqual(ticket['preconditions']['first_body_hash'],observation.digest(self.source_body))

    async def test_cancel_overlay_does_not_undo_data_budget_or_grant_another_retry(self):
        overlay=await self.approve();await self.retry()
        async with self.sessions() as s:
            used=(await s.get(ActionExecutionControl,'exec-durable')).requests_used
        result=await self.store.cancel(project_id=7,execution_id='exec-durable',overlay_id=overlay['overlay_id'],
            initiator='operator',rationale='restore configuration only')
        self.assertEqual(result['status'],'cancelled');self.assertFalse(result['data_rollback'])
        async with self.sessions() as s:self.assertEqual((await s.get(ActionExecutionControl,'exec-durable')).requests_used,used)
        with self.assertRaises(ValueError):await self.store.recover(project_id=7,execution_id='exec-durable',
            incident_id='source-incident',node_key='n1',overlay_id=overlay['overlay_id'],idempotency_key='second-recovery',
            initiator='operator',rationale='cannot grant another attempt')

    async def test_arbitrary_parameters_and_provider_rejected_even_if_body_valid(self):
        base=self.profiles.CATALOG['synthetic-json-v1']
        for extra in ({'temperature':9},{'provider':'external'},{'kind':'image'},{'max_output_tokens':90000}):
            with self.assertRaises(ValueError):self.profiles.effective_body(self.source_body,{**base,**extra})

    async def test_non_loopback_transport_denied(self):
        with self.assertRaisesRegex(ValueError,'loopback'):self.profiles.synthetic_transport('https://provider.invalid','synthetic-key')

    async def test_stale_catalog_candidate_and_cancelled_versions_cannot_reserve(self):
        overlay=await self.approve()
        await self.store.cancel(project_id=7,execution_id='exec-durable',overlay_id=overlay['overlay_id'],initiator='operator',rationale='cancel version')
        with self.assertRaisesRegex(ValueError,'consumed'):await self.store.recover(project_id=7,execution_id='exec-durable',
            incident_id='source-incident',node_key='n1',overlay_id=overlay['overlay_id'],idempotency_key='cancelled-version',initiator='operator',rationale='synthetic')

    async def test_retry_same_still_blocks_changed_effective_body_before_send(self):
        from types import SimpleNamespace
        from app.services.actions_recovery import check_body
        ticket=await self.retry()
        async with self.sessions() as s:scope=await s.get(observation.ExecutionControl,'exec-durable')
        ctx=SimpleNamespace(observer=observation._observer,scope=scope,attempt_id=ticket['new_attempt_id'],last_request_id=None)
        with self.assertRaisesRegex(RuntimeError,'input changed'):
            await check_body(ctx,observation.digest({**self.source_body,'model':'other'}))
        await check_body(ctx,observation.digest(self.source_body))

    async def test_cancel_queued_profile_holds_execution_without_refunding_budget(self):
        overlay=await self.approve()
        ticket=await self.store.recover(project_id=7,execution_id='exec-durable',incident_id='source-incident',node_key='n1',
            overlay_id=overlay['overlay_id'],idempotency_key='queued-profile',initiator='operator',rationale='synthetic')
        result=await self.store.cancel(project_id=7,execution_id='exec-durable',overlay_id=overlay['overlay_id'],initiator='operator',rationale='hold queued attempt')
        async with self.sessions() as s:
            self.assertTrue((await s.get(ActionExecutionControl,'exec-durable')).held)
            self.assertEqual((await s.get(RecoveryAction,ticket['action_id'])).status,'human_review')
        self.assertFalse(result['budget_reset'])

    async def test_profile_schema_only_allows_approved_id_no_arbitrary_model(self):
        from app.services.decision_v1 import validate_proposal
        ctx={'context_version':'v','evidence_ids':['proof'],'allowed_actions':['retry_profile'],
            'approved_profiles':[{'overlay_id':'allowed'}]}
        proposal={'action':'retry_profile','arguments':{'overlay_id':'allowed'},'evidence_ids':['proof'],
            'context_version':'v','explanation':'approved candidate'}
        self.assertEqual(validate_proposal(json.dumps(proposal),ctx)['arguments'],{'overlay_id':'allowed'})
        for args in ({'overlay_id':'foreign'},{'overlay_id':'allowed','model':'custom'}):
            with self.assertRaises(ValueError):validate_proposal(json.dumps({**proposal,'arguments':args}),ctx)

def load_tests(loader,tests,pattern):
    return unittest.TestSuite(Profiles(name) for name in Profiles.__dict__ if name.startswith('test_'))

if __name__=='__main__':unittest.main()
