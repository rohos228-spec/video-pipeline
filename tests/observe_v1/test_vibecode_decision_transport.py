"""Vibecode decision transport: one non-stream POST, parsing, completeness. httpx.MockTransport only."""
import asyncio,json,unittest
from unittest.mock import patch
import httpx
from app.services import gpt_api
from app.services.gpt_api import GptChatResult,GptApiError
from app.settings import settings

KEY='vk-test-fixture-0000'
URL='https://vibecode.moe/v1/chat/completions'

def reply(content='{}',finish='stop',usage=None,**extra):
    return {'id':'chatcmpl-x','choices':[{'message':{'role':'assistant','content':content},'finish_reason':finish}],
            'usage':usage if usage is not None else {'prompt_tokens':10,'completion_tokens':5},**extra}


class Transport(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service=__import__('app.services.decision_v1',fromlist=['x'])
        self.calls=[]

    def mock(self,handler):
        def wrapped(request):
            self.calls.append(request);return handler(request)
        return patch.object(gpt_api,'_async_client',lambda **kw:httpx.AsyncClient(transport=httpx.MockTransport(wrapped),timeout=kw.get('timeout')))

    async def post(self,**body):
        return await self.service.vibecode_post(url=URL,headers={'Authorization':'Bearer '+KEY},
            body={'model':'gpt-5.6-sol','messages':[],**body},timeout=30,use_model='gpt-5.6-sol')

    async def test_success_maps_text_usage_finish_and_id(self):
        with self.mock(lambda r:httpx.Response(200,json=reply('{"a":1}'))):
            result=await self.post(stream=False)
        self.assertEqual((result.text,result.finish_reason,result.response_id),('{"a":1}','stop','chatcmpl-x'))
        self.assertEqual(result.usage,{'prompt_tokens':10,'completion_tokens':5})
        self.assertEqual(len(self.calls),1)

    async def test_request_is_non_stream_with_bearer_and_no_stream_options(self):
        with self.mock(lambda r:httpx.Response(200,json=reply())):
            await self.post(stream=False,max_completion_tokens=512)
        req=self.calls[0];sent=json.loads(req.content)
        self.assertEqual(req.headers['authorization'],'Bearer '+KEY)
        self.assertIs(sent['stream'],False);self.assertNotIn('stream_options',sent)
        self.assertEqual(sent['max_completion_tokens'],512)

    async def test_http_500_is_one_call_and_not_retried(self):
        with self.mock(lambda r:httpx.Response(500,text='boom')),self.assertRaises(GptApiError):
            await self.post()
        self.assertEqual(len(self.calls),1)

    async def test_redirect_is_not_followed(self):
        with self.mock(lambda r:httpx.Response(307,headers={'location':'https://evil.invalid/x'})),self.assertRaises(Exception):
            await self.post()
        self.assertEqual(len(self.calls),1)

    async def test_envelope_error_in_http_200(self):
        with self.mock(lambda r:httpx.Response(200,json={'error':{'message':'insufficient_quota','code':'insufficient_quota'}})):
            with self.assertRaises(GptApiError) as ctx:await self.post()
        self.assertEqual(ctx.exception.context['error_kind'],'provider_envelope');self.assertFalse(ctx.exception.retryable)

    async def test_non_json_body_is_error(self):
        with self.mock(lambda r:httpx.Response(200,text='<html>')),self.assertRaisesRegex(ValueError,'not JSON'):
            await self.post()

    async def test_empty_content_with_length_becomes_truncated_result(self):
        with self.mock(lambda r:httpx.Response(200,json=reply('','length',{'prompt_tokens':9,'completion_tokens':2048}))):
            result=await self.post()
        self.assertEqual((result.text,result.finish_reason),('','length'))
        self.assertEqual(self.service.completion_state('vibecode_chat_completions',result,2048),(False,True))

    async def test_empty_content_with_stop_stays_an_error(self):
        with self.mock(lambda r:httpx.Response(200,json=reply('','stop'))),self.assertRaises(GptApiError):
            await self.post()

    async def test_url_builder_targets_vibecode_and_relay(self):
        from app.services.llm_override import NodeLlmOverride,use_override
        ov=NodeLlmOverride('gpt-5.6-sol','stable','text','vibecode','x',None)
        with patch.object(settings,'gpt_relay_token',''),patch.object(settings,'vibecode_base_url','https://vibecode.moe/v1'),use_override(ov):
            self.assertEqual(gpt_api._chat_url('gpt-5.6-sol'),URL)
        with patch.object(settings,'gpt_relay_token','t'),patch.object(settings,'gpt_base_url','https://relay.example'),use_override(ov):
            self.assertEqual(gpt_api._chat_url('gpt-5.6-sol'),'https://relay.example/v1/chat/completions')

    async def test_headers_use_only_vibecode_key(self):
        from app.services.llm_override import NodeLlmOverride,use_override
        ov=NodeLlmOverride('gpt-5.6-sol','stable','text','vibecode','x',None)
        with patch.object(settings,'vibecode_api_key',KEY),patch.object(settings,'gpt_api_key','kie-other-key'),use_override(ov):
            self.assertEqual(gpt_api._headers()['Authorization'],'Bearer '+KEY)
        with patch.object(settings,'vibecode_api_key',''),use_override(ov),self.assertRaises(GptApiError):
            gpt_api._headers()


class CompletionState(unittest.TestCase):
    def setUp(self):self.service=__import__('app.services.decision_v1',fromlist=['x'])

    def state(self,kind,raw,finish='',usage=None,cap=1000):
        return self.service.completion_state(kind,GptChatResult(text='t',model='m',finish_reason=finish,usage=usage or {},raw=raw),cap)

    def test_chat_stop_is_complete(self):
        self.assertEqual(self.state('vibecode_chat_completions',reply(),'stop'),(True,False))

    def test_chat_finish_from_raw_choices_when_result_field_empty(self):
        self.assertEqual(self.state('vibecode_chat_completions',reply(finish='stop')),(True,False))

    def test_chat_length_is_truncated(self):
        self.assertEqual(self.state('vibecode_chat_completions',reply(finish='length'),'length'),(False,True))

    def test_chat_other_finish_reasons_are_uncertain_not_truncated(self):
        for finish in ('content_filter','tool_calls',''):
            with self.subTest(finish=finish):
                self.assertEqual(self.state('vibecode_chat_completions',reply(finish=finish),finish),(False,False))

    def test_chat_completion_tokens_at_cap_marks_truncated_even_with_stop(self):
        self.assertEqual(self.state('vibecode_chat_completions',reply(),'stop',{'completion_tokens':1000}),(False,True))
        self.assertEqual(self.state('vibecode_chat_completions',reply(),'stop',{'completion_tokens':999}),(True,False))

    def test_chat_salvaged_is_incomplete(self):
        self.assertEqual(self.state('vibecode_chat_completions',{**reply(),'sse_salvaged':True},'stop'),(False,False))

    def test_kie_rules_unchanged(self):
        self.assertEqual(self.state('kie_responses',{'status':'completed'}),(True,False))
        self.assertEqual(self.state('kie_responses',{'status':'in_progress'}),(False,False))
        self.assertEqual(self.state('kie_responses',{'status':'completed','sse_salvaged':True}),(False,False))
        self.assertEqual(self.state('kie_responses',{'status':'incomplete'}),(False,True))
        # Kie never trusts a bare finish_reason without a Responses status.
        self.assertEqual(self.state('kie_responses',{},'stop'),(False,False))
        self.assertEqual(self.state('kie_responses',{'status':'completed'},usage={'completion_tokens':5000}),(True,False))


if __name__=='__main__':unittest.main()
