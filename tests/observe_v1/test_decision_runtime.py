"""Production adapter safety, exercised offline; no real credentials."""
import copy,importlib,json,unittest
from unittest.mock import patch,AsyncMock
from pathlib import Path
import test_decision_v1 as base_tests
import test_profiles_v1 as profile_tests
from app.services import observation

def configured(**extra):
    d={'version':1,'mode':'production','online_enabled':True,
       'endpoint':'http://127.0.0.1:18766/codex/v1/responses',
       'decision':{'catalog_id':'gpt-kie','model_id':'gpt-5-6-sol','reasoning_effort':'low','max_output_tokens':1024,'timeout_s':30},
       'profiles':{},'allowed_actions':['read_events','read_contract','retry_same','human_review','wait','finish']}
    d.update(extra);return d

class RuntimeConfig(unittest.TestCase):
    def setUp(self):self.config=importlib.import_module('app.services.decision_config')
    def test_explicit_production_adapter_uses_existing_catalog(self):
        cfg=self.config.validate_config(configured());self.assertEqual(cfg['decision']['model_id'],'gpt-5-6-sol')
    def test_disabled_example_cannot_send(self):
        with self.assertRaisesRegex(ValueError,'disabled'):
            self.config.check_route(self.config.validate_config(configured(online_enabled=False)),configured()['endpoint'])
    def test_empty_production_profile_allowlist_is_not_synthetic(self):
        self.assertEqual(self.config.validate_config(configured())['profiles'],{})
    def test_synthetic_names_not_valid_production_models(self):
        d=configured();d['decision']['model_id']='gpt-synthetic-alt'
        with self.assertRaises(ValueError):self.config.validate_config(d)
    def test_unknown_provider_catalog_rejected(self):
        d=configured();d['decision']['catalog_id']='unapproved-provider'
        with self.assertRaises(ValueError):self.config.validate_config(d)
    def test_extra_parameters_and_arbitrary_prompt_rejected(self):
        for key in ('temperature','system','shell'):
            d=configured();d['decision'][key]='arbitrary'
            with self.assertRaises(ValueError):self.config.validate_config(d)
    def test_output_token_limit_is_positive_bounded(self):
        for n in (0,-1,999999,True):
            d=configured();d['decision']['max_output_tokens']=n
            with self.assertRaises(ValueError):self.config.validate_config(d)
    def test_route_change_is_not_approved(self):
        with self.assertRaises(ValueError):self.config.check_route(configured(),'https://other.invalid/codex/v1/responses')
    def test_non_https_external_route_is_rejected(self):
        with self.assertRaises(ValueError):self.config.validate_config(configured(endpoint='http://outside.invalid/codex/v1/responses'))
    def test_diagnostic_configuration_forbids_write_tools(self):
        d=configured();d['allowed_actions']=['read_events','read_contract','human_review']
        cfg=self.config.validate_config(d);self.assertNotIn('retry_same',cfg['allowed_actions'])
    def test_production_profile_is_explicit_and_catalog_checked(self):
        d=configured();d['profiles']={'approved-json':{'model_id':'gpt-5-6-sol','provider':'kie','kind':'text','channel':'stable','reasoning_effort':'medium','max_output_tokens':2048,'prompt_variant':'require-apply-ops-v1','synthetic_only':False}}
        self.assertIn('approved-json',self.config.validate_config(d)['profiles'])
    def test_unknown_profile_parameters_rejected(self):
        d=configured();d['profiles']={'bad':{'model_id':'gpt-synthetic-alt'}}
        with self.assertRaises(ValueError):self.config.validate_config(d)
    def test_default_example_is_disabled_and_valid(self):
        raw=json.loads((Path(__file__).resolve().parents[2]/'config/orchestrator-runtime.example.json').read_text())
        cfg=self.config.validate_config(raw)
        self.assertEqual(cfg['profiles'],{});self.assertEqual(cfg['allowed_actions'],[])
        with self.assertRaises(ValueError):self.config.check_route(cfg,'https://unapproved.invalid/codex/v1/responses')
    def test_production_catalog_does_not_reuse_test_profiles(self):
        from app.services import profiles_v1
        with patch.object(self.config,'runtime_config',return_value=configured()):
            self.assertEqual(profiles_v1.active_catalog(),{})
            with self.assertRaises(ValueError):profiles_v1.validate_candidate(profiles_v1.CATALOG['synthetic-json-v1'])
    def test_explicit_production_profile_transformation_is_bounded(self):
        from app.services import profiles_v1
        d=configured();candidate={'model_id':'gpt-5-6-sol','provider':'kie','kind':'text','channel':'stable','reasoning_effort':'medium','max_output_tokens':2048,'prompt_variant':'require-apply-ops-v1','synthetic_only':False}
        d['profiles']={'approved-json':candidate}
        with patch.object(self.config,'runtime_config',return_value=self.config.validate_config(d)):
            profiles_v1.validate_candidate(candidate)
            body=profiles_v1.effective_body({'input':'original contract','model':'gpt-5-6-sol'},candidate)
            self.assertTrue(body['input'].startswith('original contract'))
            self.assertEqual(body['max_output_tokens'],2048);self.assertEqual(body['reasoning'],{'effort':'medium'})

class RuntimeAdapter(base_tests.DecisionDurability):
    async def asyncSetUp(self):
        await super().asyncSetUp();self.config=importlib.import_module('app.services.decision_config')
        self.runtime=patch.object(self.config,'runtime_config',return_value=configured());self.runtime_mock=self.runtime.start();self.addCleanup(self.runtime.stop)
    async def test_config_binding_persists_and_stale_configuration_stops(self):
        await self.enable_decisions();d=await self.begin();token=await self.loop.claim(d['decision_id'])
        async with self.sessions() as s:
            binding=await s.get(self.config.DecisionRuntimeBinding,'exec-durable');self.assertEqual(binding.config['mode'],'production')
        changed=configured();changed['decision']['reasoning_effort']='high';self.runtime.stop()
        with patch.object(self.config,'runtime_config',return_value=changed):
            async with self.sessions() as s:
                row=await s.get(self.service.DecisionRecord,d['decision_id'])
                with self.assertRaisesRegex(ValueError,'configuration'):await self.loop._live(s,row,token)
    async def test_read_only_policy_removes_recovery_from_context(self):
        self.runtime_mock.return_value['allowed_actions']=['read_events','read_contract','human_review']
        await self.enable_decisions();d=await self.begin();ctx=await self.loop.context(d['decision_id'])
        self.assertNotIn('retry_same',ctx['allowed_actions']);self.assertNotIn('retry_profile',ctx['allowed_actions'])
    async def test_real_client_boundary_receives_model_and_token_limit(self):
        from app.services.gpt_api import GptChatResult
        captured=[]
        async def transport(**kw):
            captured.append(kw)
            hook=__import__('app.services.decision_transport',fromlist=['current']).current.get()
            async def send(**args):return GptChatResult(text='{}',model=args['use_model'],finish_reason='stop',usage={},raw={'status':'completed'})
            return await hook.send(send,(),{'url':configured()['endpoint'],'headers':{'Authorization':'Bearer synthetic-fixture'},'body':{'model':'gpt-5-6-sol','input':'context','stream':True},'use_model':'gpt-5-6-sol'})
        await self.enable_decisions();d=await self.begin()
        with patch('app.services.gpt_api.chat',side_effect=transport):await self.loop.step(d['decision_id'])
        self.assertEqual(captured[0]['model'],'gpt-5-6-sol')
        async with self.sessions() as s:
            turns=(await s.scalars(__import__('sqlalchemy').select(self.service.DecisionTurn))).all()
            start=next(t for t in turns if t.role=='http_event' and t.payload.get('kind')=='decision_model_start')
            self.assertEqual(start.payload['metadata']['purpose'],'decision');self.assertEqual(start.payload['metadata']['max_output_tokens'],1024)

    async def test_node_user_stop_is_checked_before_http(self):
        from app.models import Project
        from app.services.gpt_api import GptChatResult
        sends=[]
        async def transport(**kw):sends.append(kw);return GptChatResult(text='fixture',model='gpt-5-6-sol',raw={'status':'completed'})
        async with self.sessions() as s:
            project=await s.get(Project,7);project.meta={**project.meta,'user_stop':True};await s.commit()
            scope=await s.get(observation.ExecutionControl,'exec-durable')
            ctx=observation.AttemptContext(observation._observer,scope,s,'project','n1','source-attempt')
            token=observation._attempt.set(ctx)
            try:
                with self.assertRaises(RuntimeError):await observation.observe_model(transport)(url=configured()['endpoint'],body={'test':1},use_model='gpt-5-6-sol')
            finally:observation._attempt.reset(token)
        self.assertEqual(sends,[])
    async def test_partial_node_response_holds_before_next_post(self):
        from app.services.gpt_api import GptChatResult
        sends=[]
        async def transport(**kw):sends.append(kw);return GptChatResult(text='partial',model='gpt-5-6-sol',raw={'status':'in_progress','sse_salvaged':True})
        async with self.sessions() as s:
            scope=await s.get(observation.ExecutionControl,'exec-durable')
            token=observation._attempt.set(observation.AttemptContext(observation._observer,scope,s,'project','n1','source-attempt'))
            try:
                for _ in range(2):
                    with self.assertRaises(RuntimeError):await observation.observe_model(transport)(url=configured()['endpoint'],body={'test':1},use_model='gpt-5-6-sol')
            finally:observation._attempt.reset(token)
        self.assertEqual(len(sends),1)
    async def test_retrieval_uses_same_durable_limit(self):
        from app.services import decision_transport
        from app.services.actions_v1 import ActionExecutionControl
        await self.enable_decisions();d=await self.begin();token=await self.loop.claim(d['decision_id']);ctx=await self.loop.context(d['decision_id'])
        hook=self.service.HttpDecision(self.loop,d['decision_id'],token,ctx['context_version'])
        async with self.sessions() as s:
            c=await s.get(ActionExecutionControl,'exec-durable');c.request_limit=1;await s.commit()
        bound=decision_transport.current.set(hook)
        try:
            await observation.authorize_model_retrieve(configured()['endpoint']+'/resp_test')
            with self.assertRaisesRegex(RuntimeError,'budget exhausted'):await observation.authorize_model_retrieve(configured()['endpoint']+'/resp_test')
        finally:decision_transport.current.reset(bound)
        async with self.sessions() as s:self.assertEqual((await s.get(ActionExecutionControl,'exec-durable')).requests_used,1)
    async def test_actual_client_retrieval_cannot_exceed_common_budget(self):
        from app.services import decision_transport,gpt_api
        from app.services.actions_v1 import ActionExecutionControl
        from contextlib import asynccontextmanager
        import httpx
        await self.enable_decisions();d=await self.begin();token=await self.loop.claim(d['decision_id']);ctx=await self.loop.context(d['decision_id'])
        hook=self.service.HttpDecision(self.loop,d['decision_id'],token,ctx['context_version'])
        async with self.sessions() as s:
            c=await s.get(ActionExecutionControl,'exec-durable');c.request_limit=1;await s.commit()
        calls=[]
        async def get(url,**kw):
            calls.append(url);return httpx.Response(200,json={'id':'resp_test','status':'in_progress'},request=httpx.Request('GET',url))
        mock=AsyncMock();mock.get.side_effect=get
        @asynccontextmanager
        async def client(**kw):yield mock
        bound=decision_transport.current.set(hook)
        try:
            with patch.object(gpt_api,'_async_client',client):
                await gpt_api.fetch_completed_response(post_url=configured()['endpoint'],headers={},response_id='resp_test',polls=3,poll_s=0)
        finally:decision_transport.current.reset(bound)
        self.assertEqual(len(calls),1)
        async with self.sessions() as s:self.assertEqual((await s.get(ActionExecutionControl,'exec-durable')).requests_used,1)
    async def test_worker_body_audit_failure_prevents_transport(self):
        sends=[]
        async def transport(**kw):sends.append(kw)
        async with self.sessions() as s:
            scope=await s.get(observation.ExecutionControl,'exec-durable')
            bound=observation._attempt.set(observation.AttemptContext(observation._observer,scope,s,'project','n1','source-attempt'))
            try:
                with patch.object(observation._observer,'save_body',return_value={'gap':'disk_failure'}):
                    with self.assertRaises(RuntimeError):await observation.observe_model(transport)(url=configured()['endpoint'],body={'test':1},use_model='gpt-5-6-sol')
            finally:observation._attempt.reset(bound)
        self.assertEqual(sends,[])

    async def test_actual_auto_review_uses_shared_http_budget(self):
        from app.orchestrator.auto_advance import _run_text_review
        from app.models import Project,HITLKind
        from app.services.gpt_api import GptChatResult
        from app.services.actions_v1 import ActionExecutionControl
        sends=[]
        async def transport(**kw):
            sends.append(kw);return GptChatResult(text='fixture',model='gpt-5-6-sol',raw={'status':'completed'})
        async def review(**kw):
            return await observation.observe_model(transport)(url=configured()['endpoint'],body={'input':'review'},use_model='gpt-5-6-sol')
        async with self.sessions() as s:
            c=await s.get(ActionExecutionControl,'exec-durable');c.request_limit=1;await s.commit()
            project=await s.get(Project,7)
            with patch('app.services.gpt_client.get_gpt_client',return_value=AsyncMock()),patch('app.services.auto_review.review_plan',side_effect=review):
                await _run_text_review(project,HITLKind.approve_plan,'synthetic plan')
                with self.assertRaisesRegex(RuntimeError,'budget exhausted'):
                    await _run_text_review(project,HITLKind.approve_plan,'synthetic plan')
        self.assertEqual(len(sends),1)
        async with self.sessions() as s:
            self.assertEqual((await s.get(ActionExecutionControl,'exec-durable')).requests_used,1)
            facts=(await s.scalars(__import__('sqlalchemy').select(observation.ObservationOutbox))).all()
            self.assertTrue(any(x.kind=='model_start' and x.payload.get('purpose')=='review' for x in facts))

    async def test_review_outside_managed_scope_cannot_send(self):
        from app.orchestrator.auto_advance import _run_text_review
        from app.models import Project,HITLKind
        sends=AsyncMock()
        async with self.sessions() as s:
            project=await s.get(Project,7)
            with patch('app.services.gpt_client.get_gpt_client',return_value=AsyncMock()),patch('app.services.auto_review.review_script',sends):
                with self.assertRaisesRegex(RuntimeError,'scope'):
                    await _run_text_review(project,HITLKind.approve_script,'synthetic script')
        sends.assert_not_awaited()

# Inherit fixtures, not historical suites.
for _name in list(dir(RuntimeAdapter)):
    if _name.startswith('test_') and _name not in RuntimeAdapter.__dict__:setattr(RuntimeAdapter,_name,None)

class RuntimeProfiles(profile_tests.Profiles):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.config=importlib.import_module('app.services.decision_config')
        candidate={'model_id':'gpt-5-6-sol','provider':'kie','kind':'text','channel':'stable','reasoning_effort':'medium','max_output_tokens':2048,'prompt_variant':'require-apply-ops-v1','synthetic_only':False}
        self.cfg=configured(profiles={'approved-json':candidate})
        self.runtime=patch.object(self.config,'runtime_config',return_value=self.cfg)
        self.runtime.start();self.addCleanup(self.runtime.stop)
    async def approve_production(self):
        state=await self.store.state(7,'exec-durable','n1')
        return await self.store.approve(project_id=7,execution_id='exec-durable',node_key='n1',profile_id='approved-json',
            base_version=state['base_version'],catalog_hash=state['catalog_hash'],initiator='synthetic-operator',rationale='offline production policy')
    async def test_explicit_production_overlay_persists_exact_candidate(self):
        overlay=await self.approve_production()
        self.assertEqual(overlay['candidate'],self.cfg['profiles']['approved-json'])
        state=await self.store.state(7,'exec-durable','n1')
        self.assertFalse(state['synthetic_only']);self.assertEqual(len(state['overlays']),1)
    async def test_production_catalog_revision_revokes_old_candidate(self):
        await self.approve_production()
        old=(await self.store.state(7,'exec-durable','n1'))['catalog_hash']
        self.cfg['profiles']['approved-json']['reasoning_effort']='high'
        state=await self.store.state(7,'exec-durable','n1')
        self.assertNotEqual(old,state['catalog_hash'])
        with self.assertRaisesRegex(ValueError,'catalog'):
            await self.store.approve(project_id=7,execution_id='exec-durable',node_key='n1',profile_id='approved-json',
                base_version=state['base_version'],catalog_hash=old,initiator='operator',rationale='old policy')
    async def test_synthetic_candidate_cannot_be_approved_in_production_mode(self):
        state=await self.store.state(7,'exec-durable','n1')
        with self.assertRaises(ValueError):
            await self.store.approve(project_id=7,execution_id='exec-durable',node_key='n1',profile_id='synthetic-json-v1',
                base_version=state['base_version'],catalog_hash=state['catalog_hash'],initiator='operator',rationale='not approved')

for _name in dir(RuntimeProfiles):
    if _name.startswith('test_') and _name not in RuntimeProfiles.__dict__:setattr(RuntimeProfiles,_name,None)
if __name__=='__main__':unittest.main()
