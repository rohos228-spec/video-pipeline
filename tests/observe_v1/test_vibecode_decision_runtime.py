"""Vibecode decision adapter through HttpDecision / DecisionSupervisor.step; stubs only, no network."""
import importlib,json,unittest
from unittest.mock import patch,MagicMock
import httpx
from sqlalchemy import select
import test_decision_v1 as base_tests
from app.services import observation,gpt_api
from app.services.gpt_api import GptChatResult,GptApiError
from app.settings import settings

KEY='vk-test-fixture-0000'
ENDPOINT='https://vibecode.moe/v1/chat/completions'
CAP=2048

def vibecode(**extra):
    d={'version':1,'mode':'production','online_enabled':True,'endpoint':ENDPOINT,
       'model_transport':'real','external_calls_enabled':True,'retrieval_allowed':False,'continuation_allowed':False,
       'decision':{'catalog_id':'gpt-5.6-sol-vibecode','model_id':'gpt-5.6-sol','reasoning_effort':None,
                   'max_output_tokens':CAP,'timeout_s':60},
       'profiles':{},'allowed_actions':['read_events','read_contract','retry_same','human_review','wait','finish']}
    d.update(extra);return d

def chat_reply(content='{}',finish='stop',usage=None):
    return {'id':'chatcmpl-x','choices':[{'message':{'role':'assistant','content':content},'finish_reason':finish}],
            'usage':usage if usage is not None else {'prompt_tokens':10,'completion_tokens':5}}

def result(content='{}',finish='stop',usage=None):
    raw=chat_reply(content,finish,usage)
    return GptChatResult(text=content,model='gpt-5.6-sol',finish_reason=finish,usage=raw['usage'],raw=raw,response_id='chatcmpl-x')


class VibecodeAdapter(base_tests.DecisionDurability):
    async def asyncSetUp(self):
        await super().asyncSetUp();self.config=importlib.import_module('app.services.decision_config')
        for target,name,value in ((settings,'vibecode_api_key',KEY),(settings,'gpt_relay_token',''),
                                  (settings,'vibecode_base_url','https://vibecode.moe/v1')):
            p=patch.object(target,name,value);p.start();self.addCleanup(p.stop)
        self.runtime=patch.object(self.config,'runtime_config',return_value=vibecode());self.runtime_mock=self.runtime.start();self.addCleanup(self.runtime.stop)
        self.track=MagicMock();p=patch.object(observation,'_track_api_cost',self.track);p.start();self.addCleanup(p.stop)
        self.requests=[];self.handler=lambda request:httpx.Response(200,json=chat_reply())

    def mock_client(self):
        def wrapped(request):
            self.requests.append(request);return self.handler(request)
        return patch.object(gpt_api,'_async_client',lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(wrapped),timeout=kw.get('timeout')))

    def proposal_handler(self,action='wait',**override):
        def handler(request):
            context=json.loads(json.loads(request.content)['messages'][-1]['content'])
            p=dict(action=action,arguments={},evidence_ids=[],context_version=context['context_version'],
                   explanation='synthetic facts, no hidden reasoning');p.update(override)
            return httpx.Response(200,json=chat_reply(json.dumps(p)))
        return handler

    async def hook(self):
        await self.enable_decisions();d=await self.begin();token=await self.loop.claim(d['decision_id'])
        ctx=await self.loop.context(d['decision_id'])
        return d,self.service.HttpDecision(self.loop,d['decision_id'],token,ctx['context_version'])

    async def fresh(self):
        # step() claims the decision itself, so these tests must not pre-claim it.
        await self.enable_decisions();return await self.begin()

    def kwargs(self,**extra):
        return {'url':ENDPOINT,'headers':{'Authorization':'Bearer '+KEY},'body':{'model':'gpt-5.6-sol','messages':[]},
                'use_model':'gpt-5.6-sol',**extra}

    async def used(self):
        from app.services.actions_v1 import ActionExecutionControl
        async with self.sessions() as s:return (await s.get(ActionExecutionControl,'exec-durable')).requests_used

    async def turns(self):
        async with self.sessions() as s:return (await s.scalars(select(self.service.DecisionTurn))).all()

    async def facts(self,kind):
        return [t.payload for t in await self.turns() if t.role=='http_event' and t.payload.get('kind')==kind]

    async def status(self,decision_id):
        async with self.sessions() as s:return await s.get(self.service.DecisionRecord,decision_id)

    # --- full step: one non-stream POST -------------------------------------------------
    async def test_step_makes_one_nonstream_vibecode_post(self):
        d=await self.fresh();self.handler=self.proposal_handler('wait')
        with self.mock_client(),patch('app.services.gpt_api.chat',side_effect=AssertionError('Kie client must not be used')):
            await self.loop.step(d['decision_id'])
        self.assertEqual(len(self.requests),1)
        req=self.requests[0];sent=json.loads(req.content)
        self.assertEqual(str(req.url),ENDPOINT);self.assertEqual(req.headers['authorization'],'Bearer '+KEY)
        self.assertEqual(sent['model'],'gpt-5.6-sol');self.assertIs(sent['stream'],False);self.assertEqual(sent['max_completion_tokens'],CAP)
        for absent in ('reasoning','reasoning_effort','max_output_tokens','max_tokens','response_format','temperature','stream_options'):
            self.assertNotIn(absent,sent)
        self.assertEqual([m['role'] for m in sent['messages']][0],'system');self.assertEqual(sent['messages'][-1]['role'],'user')
        self.assertEqual(await self.used(),1)
        self.assertEqual((await self.status(d['decision_id'])).status,'waiting_evidence')
        start=(await self.facts('decision_model_start'))[0]['metadata']
        self.assertEqual((start['provider'],start['route_kind'],start['route']),('vibecode','vibecode_chat_completions',ENDPOINT))
        self.assertIsNone(start['reasoning_effort'])
        self.assertNotIn('vk-test',json.dumps([t.payload for t in await self.turns()],default=str))

    async def test_step_cost_tracked_with_provider_vibecode(self):
        d=await self.fresh();self.handler=self.proposal_handler('wait')
        with self.mock_client():await self.loop.step(d['decision_id'])
        self.track.assert_called_once()
        kw=self.track.call_args.kwargs
        self.assertEqual(kw['provider'],'vibecode');self.assertEqual(kw['usage'],{'prompt_tokens':10,'completion_tokens':5})
        self.assertIsNotNone(kw['project_id']);self.assertEqual(kw.get('status_code',200),200)

    async def test_step_through_relay_uses_relay_url_and_token(self):
        relay_url='https://relay.example/v1/chat/completions'
        with patch.object(settings,'gpt_relay_token','relay-token-fixture'),patch.object(settings,'gpt_base_url','https://relay.example'):
            cfg=self.config.validate_config(vibecode(endpoint=relay_url));self.runtime_mock.return_value=cfg
            d=await self.fresh();self.handler=self.proposal_handler('wait')
            with self.mock_client():await self.loop.step(d['decision_id'])
        self.assertEqual(str(self.requests[0].url),relay_url)
        self.assertEqual(self.requests[0].headers['x-vp-relay-token'],'relay-token-fixture')

    async def test_step_url_mismatch_with_config_is_rejected_before_charge(self):
        self.runtime_mock.return_value=vibecode(endpoint='https://relay.example/v1/chat/completions')
        d=await self.fresh()
        with self.mock_client():await self.loop.step(d['decision_id'])
        self.assertEqual(self.requests,[]);self.assertEqual(await self.used(),0)
        self.assertEqual((await self.status(d['decision_id'])).status,'human_review')

    async def test_http_500_is_single_post_and_escalates(self):
        d=await self.fresh();self.handler=lambda r:httpx.Response(500,text='upstream')
        with self.mock_client():
            await self.loop.step(d['decision_id']);await self.loop.step(d['decision_id'])
        self.assertEqual(len(self.requests),1);self.assertEqual(await self.used(),1)
        row=await self.status(d['decision_id']);self.assertEqual(row.status,'human_review')
        self.assertIn('HTTP 500',row.policy_result['reason'])

    async def test_missing_key_blocks_before_any_post_or_charge(self):
        d=await self.fresh()
        with patch.object(settings,'vibecode_api_key',''),self.mock_client():await self.loop.step(d['decision_id'])
        self.assertEqual(self.requests,[]);self.assertEqual(await self.used(),0)
        row=await self.status(d['decision_id']);self.assertEqual(row.status,'human_review')
        self.assertNotIn(KEY,row.policy_result['reason'])

    async def test_truncated_answer_through_step_has_distinct_reason_and_no_repost(self):
        d=await self.fresh()
        self.handler=lambda r:httpx.Response(200,json=chat_reply('{"action":"wa','length',{'prompt_tokens':7,'completion_tokens':CAP}))
        with self.mock_client():
            await self.loop.step(d['decision_id']);await self.loop.step(d['decision_id'])
        self.assertEqual(len(self.requests),1);self.assertEqual(await self.used(),1)
        row=await self.status(d['decision_id']);self.assertEqual(row.status,'human_review')
        self.assertIn('truncated',row.policy_result['reason']);self.assertNotIn('uncertain',row.policy_result['reason'])
        self.assertEqual(self.track.call_count,1)

    async def repair_case(self,**override):
        d=await self.fresh();self.handler=self.proposal_handler(**override)
        with self.mock_client():await self.loop.step(d['decision_id'])
        row=await self.status(d['decision_id'])
        self.assertEqual(row.status,'ready');self.assertFalse(row.proposal)
        repairs=[t for t in await self.turns() if t.payload.get('action')=='repair_decision_json']
        self.assertEqual(len(repairs),1);self.assertEqual(await self.used(),1)

    async def test_action_outside_server_allow_list_is_repaired_not_executed(self):
        await self.repair_case(action='retry_profile')

    async def test_unknown_action_is_repaired_not_executed(self):
        await self.repair_case(action='shell')

    async def test_stale_context_version_is_repaired(self):
        await self.repair_case(context_version='old')

    async def test_unknown_field_is_repaired(self):
        await self.repair_case(project_id=7)

    async def test_markdown_fenced_json_is_repaired_not_auto_cleaned(self):
        d=await self.fresh();self.handler=lambda r:httpx.Response(200,json=chat_reply('```json\n{}\n```'))
        with self.mock_client():await self.loop.step(d['decision_id'])
        self.assertEqual((await self.status(d['decision_id'])).status,'ready')

    # --- HttpDecision.send directly -----------------------------------------------------
    async def test_send_body_has_token_cap_and_no_reasoning(self):
        d,hook=await self.hook();seen=[]
        async def send(**kw):seen.append(kw);return result()
        await hook.send(send,(),self.kwargs())
        body=seen[0]['body']
        self.assertEqual(body['max_completion_tokens'],CAP);self.assertEqual(body['model'],'gpt-5.6-sol')
        for absent in ('reasoning','max_output_tokens','response_format','temperature'):self.assertNotIn(absent,body)

    async def test_send_stop_without_status_is_complete(self):
        d,hook=await self.hook()
        async def send(**kw):return result()
        self.assertEqual((await hook.send(send,(),self.kwargs())).finish_reason,'stop')
        self.assertEqual(await self.used(),1);self.assertEqual(hook.response_id,'chatcmpl-x')
        end=(await self.facts('decision_model_end'))[0]['metadata']
        self.assertEqual((end['complete'],end['finish_reason']),(True,'stop'))

    async def test_send_length_raises_distinct_truncation_without_second_cost_row(self):
        d,hook=await self.hook()
        async def send(**kw):return result('{"act','length',{'prompt_tokens':3,'completion_tokens':100})
        with self.assertRaises(self.service.DecisionTruncated):await hook.send(send,(),self.kwargs())
        self.assertEqual(await self.used(),1);self.track.assert_called_once()
        self.assertEqual(self.track.call_args.kwargs['usage'],{'prompt_tokens':3,'completion_tokens':100})
        end=(await self.facts('decision_model_end'))[0]['metadata']
        self.assertEqual((end['complete'],end['problem'],end['finish_reason']),(False,'truncated','length'))
        error=(await self.facts('decision_model_error'))[0]['metadata']
        self.assertEqual((error['error_type'],error['phase'],error['complete']),('DecisionTruncated','truncated',True))
        row=await self.status(d['decision_id']);self.assertIn('truncated',row.policy_result['reason'])
        self.assertTrue(issubclass(self.service.DecisionTruncated,ValueError))

    async def test_send_completion_tokens_at_cap_marks_truncated_even_with_stop(self):
        d,hook=await self.hook()
        async def send(**kw):return result('{}','stop',{'prompt_tokens':3,'completion_tokens':CAP})
        with self.assertRaises(self.service.DecisionTruncated):await hook.send(send,(),self.kwargs())

    async def uncertain(self,finish):
        d,hook=await self.hook()
        async def send(**kw):return result('{}',finish)
        with self.assertRaisesRegex(ValueError,'uncertain') as ctx:await hook.send(send,(),self.kwargs())
        self.assertNotIsInstance(ctx.exception,self.service.DecisionTruncated)
        self.assertIn('uncertain',(await self.status(d['decision_id'])).policy_result['reason'])

    async def test_send_content_filter_finish_is_uncertain_not_truncated(self):
        await self.uncertain('content_filter')

    async def test_send_missing_finish_reason_is_uncertain(self):
        await self.uncertain('')

    async def test_send_error_before_response_records_one_failed_cost_row(self):
        d,hook=await self.hook()
        async def send(**kw):raise httpx.ReadTimeout('slow')
        with self.assertRaises(httpx.ReadTimeout):await hook.send(send,(),self.kwargs())
        self.track.assert_called_once()
        self.assertEqual((self.track.call_args.kwargs['provider'],self.track.call_args.kwargs['status_code']),('vibecode',0))
        self.assertEqual(await self.used(),1)

    async def test_second_send_denied_before_reservation(self):
        d,hook=await self.hook();calls=[]
        async def send(**kw):calls.append(kw);return result()
        await hook.send(send,(),self.kwargs())
        with self.assertRaisesRegex(ValueError,'continuation disabled'):await hook.send(send,(),self.kwargs())
        self.assertEqual(len(calls),1);self.assertEqual(await self.used(),1)

    async def test_unknown_url_rejected_before_charge(self):
        d,hook=await self.hook()
        async def send(**kw):raise AssertionError('must not send')
        for url in ('https://evil.invalid/v1/chat/completions',ENDPOINT+'?x=1','https://vibecode.moe/codex/v1/responses'):
            with self.subTest(url=url),self.assertRaisesRegex(ValueError,'route differs'):
                await hook.send(send,(),self.kwargs(url=url))
        self.assertEqual(await self.used(),0)

    async def test_model_mismatch_rejected_before_charge(self):
        d,hook=await self.hook()
        async def send(**kw):raise AssertionError('must not send')
        with self.assertRaisesRegex(ValueError,'model differs'):
            await hook.send(send,(),self.kwargs(body={'model':'gpt-5.6-terra','messages':[]}))
        self.assertEqual(await self.used(),0)

    async def test_budget_exhaustion_blocks_send(self):
        from app.services.actions_v1 import ActionExecutionControl
        d,hook=await self.hook()
        async with self.sessions() as s:
            c=await s.get(ActionExecutionControl,'exec-durable');c.request_limit=0;await s.commit()
        async def send(**kw):raise AssertionError('must not send')
        with self.assertRaisesRegex(RuntimeError,'budget exhausted'):await hook.send(send,(),self.kwargs())
        self.assertEqual(await self.used(),0)

    async def test_kie_send_keeps_responses_body_and_default_provider(self):
        self.runtime_mock.return_value=self.config.validate_config({**vibecode(),'mode':'production','endpoint':'http://127.0.0.1:18766/codex/v1/responses',
            'decision':{'catalog_id':'gpt-kie','model_id':'gpt-5-6-sol','reasoning_effort':'low','max_output_tokens':1024,'timeout_s':30},
            'model_transport':'fixture','external_calls_enabled':False})
        d,hook=await self.hook();seen=[]
        async def send(**kw):seen.append(kw);return GptChatResult(text='{}',model='gpt-5-6-sol',raw={'status':'completed'})
        await hook.send(send,(),{'url':'http://127.0.0.1:18766/codex/v1/responses','body':{'model':'gpt-5-6-sol','input':[]},'use_model':'gpt-5-6-sol'})
        self.assertEqual(seen[0]['body']['max_output_tokens'],1024);self.assertEqual(seen[0]['body']['reasoning'],{'effort':'low'})
        self.assertNotIn('max_completion_tokens',seen[0]['body'])
        self.assertNotEqual(self.track.call_args.kwargs.get('provider'),'vibecode')


# Inherit fixtures, not historical suites.
for _name in list(dir(VibecodeAdapter)):
    if _name.startswith('test_') and _name not in VibecodeAdapter.__dict__:setattr(VibecodeAdapter,_name,None)
if __name__=='__main__':unittest.main()
