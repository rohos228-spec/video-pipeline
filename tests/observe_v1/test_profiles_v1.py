"""Profiles must be immutable, scoped and consume the existing recovery ticket."""
import importlib,json,unittest
from unittest.mock import patch
from sqlalchemy import select
from app.services import observation
from app.services.actions_v1 import ActionExecutionControl
from app.services.actions_recovery import RecoveryAction
from app.services.managed_launch import ManagedLaunch
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

    async def test_profile_compatibility_uses_node_contract_not_group_name(self):
        async with self.sessions() as s:
            control=await s.get(ActionExecutionControl,'exec-durable');control.group_id='scope:approved-custom-graph';await s.commit()
        state=await self.store.state(7,'exec-durable','n1')
        self.assertTrue(state['available'],state['reason'])
        self.assertEqual((await self.approve())['status'],'approved')

    async def test_marked_scene_profiles_rejected_despite_excel_gpt_ui_type(self):
        from app.models import WorkflowRun
        for marker in ('skeleton','assemble','camera'):
            with self.subTest(marker=marker):
                async with self.sessions() as s:
                    run=await s.get(WorkflowRun,11)
                    run.nodes_snapshot=[{**n,'data':{**n.get('data',{}),'sd_agent':marker}}
                        if n['id']=='n1' else n for n in run.nodes_snapshot]
                    graph=observation.digest({'nodes':run.nodes_snapshot,'edges':run.edges_snapshot})
                    (await s.get(observation.ExecutionControl,'exec-durable')).graph_version=graph
                    (await s.get(ActionExecutionControl,'exec-durable')).graph_version=graph
                    await s.commit()
                state=await self.store.state(7,'exec-durable','n1')
                self.assertFalse(state['available'])
                self.assertEqual(state['overlays'],[])
                with self.assertRaisesRegex(ValueError,'incompatible accepted text node contract'):
                    await self.approve()

    async def test_explicit_synthetic_catalog_is_not_replaced_by_legacy_defaults(self):
        from app.services import decision_config
        cfg=decision_config.runtime_config()
        selected={'only-owner-approved':dict(self.profiles.CATALOG['synthetic-json-v1'])}
        cfg={**cfg,'profiles':selected}
        with patch.object(decision_config,'runtime_config',return_value=decision_config.validate_config(cfg)):
            self.assertEqual(self.profiles.active_catalog(),selected)
            with self.assertRaises(ValueError):await self.approve(profile_id='synthetic-json-v1')

    async def test_multiple_registered_variants_preserve_original_contract_and_roles(self):
        from app.services import decision_config
        cfg=decision_config.runtime_config()
        profiles={name:{**self.profiles.CATALOG['synthetic-json-v1'],'prompt_variant':variant,
            'reasoning_effort':'low','max_output_tokens':1024} for name,variant in
            [('keep-original','original-contract-v1'),('format-only','repair-json-only-v1')]}
        with patch.object(decision_config,'runtime_config',return_value=decision_config.validate_config({**cfg,'profiles':profiles})):
            source={**self.source_body,'input':[{'role':'system','content':'original validators'},
                {'role':'user','content':'original source facts'}]}
            unchanged=self.profiles.effective_body(source,profiles['keep-original'])
            self.assertEqual(unchanged['input'],source['input'])
            repaired=self.profiles.effective_body(source,profiles['format-only'])
            self.assertEqual(repaired['input'][0],source['input'][0])
            self.assertTrue(repaired['input'][1]['content'].startswith('original source facts'))
            self.assertEqual(source['input'][1]['content'],'original source facts')
            self.assertEqual(self.profiles.effective_body(repaired,profiles['format-only']),repaired)

    async def test_synthetic_profile_parameters_are_positive_and_bounded(self):
        from app.services import decision_config
        cfg=decision_config.runtime_config()
        for field,value in [('max_output_tokens',0),('max_output_tokens',True),('max_output_tokens',20000),
                            ('reasoning_effort','unrestricted'),('synthetic_only',False),('model_id','arbitrary')]:
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                decision_config.validate_config({**cfg,'profiles':{'unsafe':{
                    **self.profiles.CATALOG['synthetic-json-v1'],field:value}}})

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

    async def approved_launch_decision(self):
        await self.enable_decisions();record=await self.begin()
        async with self.sessions() as s:
            s.add(ManagedLaunch(launch_id='launch-policy',execution_id='exec-durable',project_id=7,
                workflow_run_id=11,idempotency_key='launch-policy-test',request_hash='test',entry_node='n1',
                status='submitted',result={},policy={'allowed_actions':['read_events','read_contract','wait',
                    'human_review','retry_same','retry_profile','finish'],'profile_ids':['synthetic-format-v1'],
                    'profile_catalog_hash':self.profiles.catalog_hash(),
                    'profiles':{'synthetic-format-v1':self.profiles.active_catalog()['synthetic-format-v1']}}))
            await s.commit()
        return record

    async def test_preapproved_choices_are_readonly_context_not_operator_overlays(self):
        record=await self.approved_launch_decision()
        context=await self.loop.context(record['decision_id'])
        self.assertEqual([p['profile_id'] for p in context['eligible_profiles']],['synthetic-format-v1'])
        self.assertIn('retry_profile',context['allowed_actions'])
        self.assertEqual((await self.store.state(7,'exec-durable','n1'))['overlays'],[])
        proposal={'action':'retry_profile','arguments':{'profile_id':'synthetic-format-v1'},
            'evidence_ids':context['evidence_ids'][:1],'context_version':context['context_version'],
            'explanation':'Use the preapproved format strategy after native exhaustion'}
        self.service.validate_proposal(json.dumps(proposal),context)
        for args in ({'profile_id':'synthetic-json-v1'},{'profile_id':'synthetic-format-v1','model':'arbitrary'}):
            with self.assertRaises(ValueError):self.service.validate_proposal(json.dumps({**proposal,'arguments':args}),context)

    async def test_legacy_execution_has_no_automatically_approved_choices(self):
        await self.enable_decisions();record=await self.begin()
        context=await self.loop.context(record['decision_id'])
        self.assertEqual(context['eligible_profiles'],[])
        with self.assertRaises(ValueError):await self.store.select_for_decision(record['decision_id'],'synthetic-format-v1')
        self.assertEqual((await self.store.state(7,'exec-durable','n1'))['overlays'],[])

    async def test_selection_requires_claimed_acting_decision_and_fresh_catalog(self):
        record=await self.approved_launch_decision()
        with self.assertRaisesRegex(ValueError,'acting'):
            await self.store.select_for_decision(record['decision_id'],'synthetic-format-v1')
        context=await self.loop.context(record['decision_id'])
        async with self.sessions() as s:
            row=await s.get(self.service.DecisionRecord,record['decision_id']);row.status='acting'
            row.lease_token='owner';row.lease_until=__import__('datetime').datetime.utcnow()+__import__('datetime').timedelta(seconds=60)
            row.context_version=context['context_version']
            row.proposal={'action':'retry_profile','arguments':{'profile_id':'synthetic-format-v1'},
                'context_version':context['context_version'],'evidence_ids':context['evidence_ids'][:1],
                'explanation':'Use the execution-approved format profile'}
            await s.commit()
        with patch.object(self.profiles,'catalog_hash',return_value='changed'):
            with self.assertRaisesRegex(ValueError,'catalog'):
                await self.store.select_for_decision(record['decision_id'],'synthetic-format-v1')
        selected=await self.store.select_for_decision(record['decision_id'],'synthetic-format-v1')
        replay=await self.store.select_for_decision(record['decision_id'],'synthetic-format-v1')
        self.assertEqual(selected['overlay_id'],replay['overlay_id'])
        self.assertTrue(any(h['kind']=='decision_selected' and h['decision_id']==record['decision_id'] for h in replay['history']))

    async def test_decision_selects_preapproved_profile_and_uses_one_recovery_ticket(self):
        record=await self.approved_launch_decision()
        from app.services.gpt_api import GptChatResult
        async def reply(**kw):
            ctx=json.loads(kw['prompt'])
            return GptChatResult(text=json.dumps({'action':'retry_profile',
                'arguments':{'profile_id':'synthetic-format-v1'},'evidence_ids':ctx['evidence_ids'][:1],
                'context_version':ctx['context_version'],'explanation':'Native retries exhausted; use approved format strategy'}),
                model='synthetic',raw={'status':'completed'})
        with patch('app.services.gpt_api.chat',side_effect=reply) as chat:
            await self.loop.step(record['decision_id'])
            await self.service.DecisionSupervisor(observation._observer).tick()
            self.assertEqual(chat.call_count,1)
        async with self.sessions() as s:
            row=await s.get(self.service.DecisionRecord,record['decision_id'])
            self.assertEqual(row.status,'waiting_worker',row.policy_result)
            tickets=(await s.scalars(select(RecoveryAction))).all()
            self.assertEqual(len(tickets),1)
            self.assertEqual(tickets[0].preconditions['action_type'],'retry_profile')
        state=await self.store.state(7,'exec-durable','n1')
        self.assertEqual(len(state['overlays']),1)
        self.assertEqual(state['overlays'][0]['profile_id'],'synthetic-format-v1')

def load_tests(loader,tests,pattern):
    return unittest.TestSuite(Profiles(name) for name in Profiles.__dict__ if name.startswith('test_'))

if __name__=='__main__':unittest.main()
