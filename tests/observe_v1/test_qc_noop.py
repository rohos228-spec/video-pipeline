from app.services import revisions
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from app.services.apply_ops_batches import run_apply_ops_batched
from app.services import gpt_api
from app.settings import settings


class QcNoopHttp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.posts = []
        self.applied = []
        self.answer = '{"ops":[]}'
        self.fail = False
        self.answers = []
        self.complete = True
        self.transport_error = None
        self.context = {'frames': [{'uuid': 'frame-one', 'number': 1,
            'voiceover_text': 'Синтетический диктор открывает дверь и выходит на улицу.',
            'fields': {'закадр': 'Синтетический диктор открывает дверь и выходит на улицу.'}}]}
        self.path = self.root / 'db_frames.json'
        self.path.write_text(json.dumps(self.context, ensure_ascii=False))
        for name, value in [('gpt_max_retries', 0), ('gpt_api_mode', 'responses'),
                            ('gpt_chat_path', '/codex/v1/responses')]:
            p = patch.object(settings, name, value)
            p.start()
            self.addCleanup(p.stop)
        def response(request):
            if request.method == 'GET': return httpx.Response(404, request=request)
            if self.transport_error: raise self.transport_error
            self.posts.append(request)
            if self.fail:
                return httpx.Response(500, request=request, content=b'synthetic provider failure')
            answer = self.answers.pop(0) if self.answers else self.answer
            events = [{'type': 'response.created', 'response': {'id': 'resp_synthetic'}},
                      {'type': 'response.output_text.delta', 'delta': answer}]
            if self.complete:
                events.append({'type': 'response.completed', 'response': {
                    'id': 'resp_synthetic', 'status': 'completed',
                    'output': [{'content': [{'type': 'output_text', 'text': answer}]}],
                    'usage': {'input_tokens': 1, 'output_tokens': 1}}})
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                content=''.join('data: ' + json.dumps(e) + '\n\n' for e in events).encode())
        def client(**kw):
            return httpx.AsyncClient(transport=httpx.MockTransport(response), **kw)
        p = patch.object(gpt_api, '_async_client', client)
        p.start()
        self.addCleanup(p.stop)

    async def batch(self, allowed=True):
        async def apply(payload): self.applied.append(payload)
        return await run_apply_ops_batched(project_dir=self.root, node_key='arbitrary-id',
            role='assist', output_mode='project_file', prompt='Built-in no-op contract',
            accompanying='', db_ctx=self.context, ctx_path=self.path, project_id=2,
            dense=False, footer_kind='shots_qc', force_full=True, allow_empty_ops=allowed,
            apply_fn=apply)

    async def test_completed_authorized_noop_uses_one_post_and_no_business_apply(self):
        result = await self.batch()
        self.assertEqual(len(self.posts), 1, 'valid QC no-op must not cause JSON retry')
        self.assertEqual(result.apply_ops, {'ops': []})
        self.assertEqual(self.applied, [])

    async def test_native_json_retry_can_accept_completed_noop_with_same_contract(self):
        self.answers=['invalid JSON','{"ops":[]}']
        result=await self.batch()
        self.assertEqual(len(self.posts),2)
        self.assertEqual(result.apply_ops,{'ops':[]})
        self.assertEqual(self.applied,[])
        body=self.posts[1].content.decode()
        self.assertIn('КОНТРАКТ QC JSON',body)
        self.assertNotIn('с непустыми полями',body)

    async def test_provider_error_is_not_successful_noop(self):
        self.fail = True
        with self.assertRaises(Exception): await self.batch()
        self.assertEqual(self.applied, [])

    async def test_malformed_json_is_not_successful_noop(self):
        self.answer = 'not valid JSON'
        with self.assertRaises(Exception): await self.batch()
        self.assertEqual(self.applied, [])

    async def test_explicit_refusal_is_not_successful_noop(self):
        self.answer = '{"ops":[],"error":"synthetic refusal"}'
        with self.assertRaises(Exception): await self.batch()
        self.assertEqual(self.applied, [])

    async def test_error_with_nonempty_ops_cannot_be_accepted(self):
        self.answer='{"error":"refused","ops":[{"frame_uuid":"frame-one","fields":{"место":"улица"}}]}'
        with self.assertRaises(Exception): await self.batch()
        self.assertEqual(self.applied,[])

    async def test_creating_node_still_rejects_empty_payload(self):
        # R0a: пустая пачка создающего узла не валит шаг, её пропускают.
        result = await self.batch(allowed=False)
        self.assertEqual(self.applied, [])
        self.assertEqual(len(self.posts), 2, 'original JSON retry remains for mandatory writes')
        self.assertTrue((result.apply_ops or {}).get('skipped_packs'))

    async def test_real_correction_reaches_original_batch_apply_callback_once(self):
        self.answer = '{"ops":[{"frame_uuid":"frame-one","fields":{"место":"улица"}}]}'
        await self.batch()
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(len(self.applied), 1)

    async def test_wrong_ops_types_and_empty_object_do_not_pass(self):
        for answer in ('{}','{"ops":null}','{"ops":{}}','{"ops":"bad"}','{"refusal":"no"}'):
            with self.subTest(answer=answer):
                self.answer=answer
                with self.assertRaises(Exception): await self.batch()
                self.assertEqual(self.applied, [])

    async def test_incomplete_noop_does_not_pass_or_repeat(self):
        self.complete=False
        with self.assertRaises(Exception): await self.batch()
        self.assertEqual(len(self.posts),1)
        self.assertEqual(self.applied,[])

    async def test_protection_exceptions_retain_type_and_do_not_repeat(self):
        from app.services.step_cancel import StepCancelledError
        for exc in (StepCancelledError('synthetic stop'), RuntimeError('mandatory audit unavailable'),
                    RuntimeError('request budget exhausted')):
            with self.subTest(error=type(exc).__name__):
                self.transport_error=exc
                with self.assertRaises(type(exc)) as caught: await self.batch()
                self.assertIs(caught.exception,exc)
                self.assertEqual(self.posts,[])
                self.assertEqual(self.applied,[])


from types import SimpleNamespace
from app.services.node_write_contract import allows_qc_noop
from app.services.observation import confirmed_qc_noop
from app.services.diagnosis import derive_incidents


def noop_facts():
    facts=[('model_start',{}),('model_end',{'complete':True}),('parse_result',{
        'payload_required':True,'payload_present':False,'ops_count':0,
        **{k:True for k in ('syntax_valid','parsed','schema_valid','ops_field_present',
                           'ops_type_valid','empty_ops_allowed','empty_ops_accepted')}}),
        ('runtime_check',{'check':'qc_noop','performed':True,'status':'passed',
            'empty_ops_allowed':True,'response_complete':True,'required_checks':[
                'strict_json_ops_array','completed_response','trusted_empty_ops_policy']}),
        ('node_change',{'transactional':True,'changes':{'status':{'after':['done']}}}),
        ('node_end',{})]
    return [dict(id=i,event_id=f'noop-{i}',execution_id='e',project_id=2,node_key='id',
        attempt_id='a',request_id='r',db_scope='project',kind=k,payload=p)
        for i,(k,p) in enumerate(facts,1)]


class NoopEvidence(unittest.TestCase):
    def test_noop_requires_complete_unsalvaged_strict_reply_and_request_identity(self):
        from app.services.gpt_operator_client import _accepted_empty_ops
        data={'ops':[]}
        for text,raw in (('{"ops":[]}',{}),('{"ops":[]}',{'status':'completed','sse_salvaged':True}),
                         ('broken',{'status':'completed'}),('[]',{'status':'completed'})):
            self.assertFalse(_accepted_empty_ops(SimpleNamespace(text=text,raw=raw),data,allowed=True))
        self.assertFalse(confirmed_qc_noop(noop_facts(),None))

    def test_policy_uses_config_not_node_name_or_prompt(self):
        p=SimpleNamespace(meta={'canvas_graph':{'nodes':[{'id':'arbitrary','type':'excel_gpt',
            'data':{'groupId':'script_frames_qc#instance'}}]},
            'prompt_slot_variants':{'arbitrary':{'main':'shots_qc_ru'}}})
        args=dict(variant='shots_qc_ru',output_mode='project_file',check_mode=False)
        self.assertTrue(allows_qc_noop(p,'arbitrary',**args))
        for key, val in [('variant','default'),('output_mode','text'),('check_mode',True)]:
            self.assertFalse(allows_qc_noop(p,'arbitrary',**{**args,key:val}))
        self.assertFalse(allows_qc_noop(p,'n_excel_gpt_fw_qc',**args))
        for group in ('foreign','QC','script_frames_qc_fake'):
            p.meta['canvas_graph']['nodes'][0]['data']['groupId']=group
            self.assertFalse(allows_qc_noop(p,'arbitrary',**args))

    def test_diagnosis_no_false_missing_payload_or_quality_claim(self):
        scope=SimpleNamespace(execution_id='e',project_id=2,node_keys=['id'],observer_state='active',gaps=[])
        rows=noop_facts()
        self.assertTrue(confirmed_qc_noop(rows,'r'))
        self.assertEqual(derive_incidents(scope,rows,{}),[])
        for remove in ('model_start','model_end','runtime_check'):
            altered=[e for e in rows if e['kind']!=remove]
            self.assertFalse(confirmed_qc_noop(altered,'r'))
            self.assertFalse(any(i['type']=='missing_payload' for i in derive_incidents(scope,altered,{})))
        other=[{**e,'attempt_id':'other'} for e in rows]
        original={**rows[2],'event_id':'bad','payload':{'payload_required':True,'parsed':False,'payload_present':False}}
        self.assertNotEqual(derive_incidents(scope,[original]+other,{})[0]['status'],'resolved_confirmed')
        db=derive_incidents(scope,rows,{'disagreement':True})
        self.assertEqual(db[0]['type'],'db_disagreement')
        self.assertNotEqual(db[0]['status'],'resolved_confirmed')

    def test_valid_json_with_rejected_schema_is_not_syntax_error(self):
        scope=SimpleNamespace(execution_id='e',project_id=2,node_keys=['id'],observer_state='active',gaps=[])
        row=noop_facts()[2]
        row['payload']={**row['payload'],'syntax_valid':True,'parsed':False,'empty_ops_accepted':False,'schema_valid':False}
        self.assertEqual(derive_incidents(scope,[row],{})[0]['type'],'missing_payload')

    def test_native_retry_to_noop_closes_only_matching_format_incident(self):
        scope=SimpleNamespace(execution_id='e',project_id=2,node_keys=['id'],observer_state='active',gaps=[])
        rows=noop_facts()
        for e in rows:e['id']+=2
        rows[0]['payload']={'previous_request_id':'r0'}
        bad={**rows[2],'id':1,'event_id':'bad-root','request_id':'r0',
             'payload':{'payload_required':True,'parsed':False,'syntax_valid':False,'payload_present':False}}
        retry={**bad,'id':2,'event_id':'retry','kind':'json_retry','payload':{'source_request_id':'r0'}}
        result=derive_incidents(scope,[bad,retry]+rows,{})
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['status'],'resolved_confirmed')
        self.assertEqual(result[0]['resolution']['correlation'],'confirmed_qc_noop_same_attempt')
        self.assertFalse(result[0]['resolution']['quality_verified'])

    def test_noop_does_not_mask_failed_required_checks(self):
        rows=noop_facts()
        for kind,payload in (('runtime_check',{'performed':True,'status':'failed'}),
                             ('node_error',{}),('domain_check',{'verdict':'reject'})):
            self.assertFalse(confirmed_qc_noop(rows+[dict(kind=kind,payload=payload,request_id='r')],'r'))


from test_actions_v1_contract import DurableActions
from contextlib import asynccontextmanager
from datetime import datetime
from sqlalchemy import select
from app.models import Project, WorkflowRun, NodeRun, NodeRunStatus
from app.services import observation
from app.services.observation import digest, ObservationEvent
from app.services.actions_recovery import RecoveryAction, reconcile
from app.services.actions_v1 import ActionExecutionControl


class NoopRecoveryVerify(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=DurableActions.asyncSetUp
    asyncTearDown=DurableActions.asyncTearDown
    exhausted_fixture=DurableActions.exhausted_fixture
    retry=DurableActions.retry

    async def verify_noop(self, *, missing_check=False, newer_attempt=False, wrong_contract=False):
        await self.exhausted_fixture()
        ticket=await self.retry()
        async with self.sessions() as s:
            row=await s.get(RecoveryAction,ticket['action_id']);row.status='await_verify'
            row.result={'worker_returned_at':'2020-01-01T00:00:00'}
            node=await s.scalar(select(NodeRun).where(NodeRun.node_key=='n1'));node.status=NodeRunStatus.done
            project=await s.get(Project,7)
            graph=[{'id':'n1','type':'excel_gpt','data':{'groupId':'script_frames_qc'}}]
            project.meta={'canvas_graph':{'nodes':graph},'prompt_slot_variants':{
                'n1':{'main':'default' if wrong_contract else 'shots_qc_ru'}}}
            run=await s.get(WorkflowRun,11);run.nodes_snapshot=graph
            control=await s.get(ActionExecutionControl,'exec-durable');control.graph_version=revisions.graph_version(graph, [])
            facts=[dict(kind='node_start',payload={},request_id=None)]+noop_facts()
            for e in facts:
                if missing_check and e['kind']=='runtime_check':continue
                s.add(ObservationEvent(event_id=__import__('uuid').uuid4().hex,execution_id='exec-durable',
                    project_id=7,workflow_run_id=11,node_key='n1',attempt_id=row.new_attempt_id,
                    request_id=e['request_id'],db_scope='project',kind=e['kind'],payload=e['payload'],created_at=datetime.utcnow()))
            if newer_attempt:
                await s.flush()
                s.add(ObservationEvent(event_id=__import__('uuid').uuid4().hex,execution_id='exec-durable',
                    project_id=7,workflow_run_id=11,node_key='n1',attempt_id='newer',db_scope='project',
                    kind='node_start',payload={},created_at=datetime.utcnow()))
            await s.commit()
        @asynccontextmanager
        async def project_session(_id):
            async with self.sessions() as s: yield s
        async def no_sync(*args): pass
        async def no_continue(*args,**kw): pass
        with patch('app.project_db.project_db_session_scope',project_session), \
             patch('app.project_db.pull_master_runtime_into_project',no_sync), \
             patch('app.project_db.push_runtime_to_master',no_sync), \
             patch('app.orchestrator.steps.enrich_xlsx._after_excel_gpt_done',no_continue):
            await reconcile(observation._observer)
        async with self.sessions() as s:
            return (await s.get(RecoveryAction,ticket['action_id'])).verification_result

    async def test_real_journal_body_limit_after_http_blocks_managed_noop(self):
        async with self.sessions() as session:
            scope=await session.get(observation.ExecutionControl,'exec-durable')
            scope.execution_id='00000000-0000-4000-8000-000000000007'
            await session.commit()
        await self.supervisor.enable(project_id=7,execution_id='00000000-0000-4000-8000-000000000007',group_id='g1',
            node_keys=['n1'],graph_version=self.graph,initiator='test',rationale='post-response audit fault',request_limit=2)
        posts=[];applied=[]
        def response(request):
            posts.append(request)
            observation._observer.MAX_TOTAL=0
            payload={'type':'response.completed','response':{'id':'resp_audit_test','status':'completed',
                'output':[{'content':[{'type':'output_text','text':'{"ops":[]}'}]}]}}
            return httpx.Response(200,headers={'content-type':'text/event-stream'},
                content=('data: '+json.dumps(payload)+'\n\n').encode(),request=request)
        def client(**kw):return httpx.AsyncClient(transport=httpx.MockTransport(response),**kw)
        async def apply(payload):applied.append(payload)
        root=Path(self.tmp.name);frames={'frames':[{'uuid':'u','voiceover_text':'synthetic voiceover','fields':{}}]}
        path=root/'input.json';path.write_text(json.dumps(frames))
        async with self.sessions() as s:
            scope=await s.get(observation.ExecutionControl,'00000000-0000-4000-8000-000000000007')
            token=observation._attempt.set(observation.AttemptContext(observation._observer,scope,s,'project','n1','audit-attempt'))
            try:
                with patch.object(gpt_api,'_async_client',client),patch.object(settings,'gpt_max_retries',0), \
                     patch.object(settings,'gpt_api_mode','responses'),patch.object(settings,'gpt_chat_path','/codex/v1/responses'):
                    with self.assertRaisesRegex(RuntimeError,'mandatory audit unavailable'):
                        await run_apply_ops_batched(project_dir=root,node_key='n1',role='assist',output_mode='project_file',
                            prompt='synthetic no-op',accompanying='',db_ctx=frames,ctx_path=path,project_id=7,
                            dense=False,footer_kind='shots_qc',force_full=True,allow_empty_ops=True,apply_fn=apply)
                self.assertEqual(len(posts),1)
                self.assertEqual(applied,[])
                self.assertIn(scope.execution_id,observation._observer.failures)
                async with self.sessions() as master:
                    facts=(await master.scalars(select(observation.ObservationOutbox).where(observation.ObservationOutbox.attempt_id=='audit-attempt'))).all()
                    self.assertFalse(any(e.kind=='runtime_check' and e.payload.get('check')=='qc_noop'
                                         and e.payload.get('status')=='passed' for e in facts))
                    self.assertTrue(any(e.payload.get('body',{}).get('gap')=='body_total_limit' for e in facts))
            finally:observation._attempt.reset(token)

    async def test_incomplete_sse_holds_managed_execution_before_next_post(self):
        execution='00000000-0000-4000-8000-000000000007'
        async with self.sessions() as session:
            scope=await session.get(observation.ExecutionControl,'exec-durable')
            scope.execution_id=execution
            await session.commit()
        await self.supervisor.enable(project_id=7,execution_id=execution,group_id='g1',
            node_keys=['n1'],graph_version=self.graph,initiator='test',rationale='incomplete SSE',request_limit=20)
        posts=[];applied=[]
        def response(request):
            if request.method=='GET':return httpx.Response(404,request=request)
            posts.append(request)
            events=[{'type':'response.created','response':{'id':'resp_incomplete'}},
                    {'type':'response.output_text.delta','delta':'{"ops":[]}'}]
            return httpx.Response(200,headers={'content-type':'text/event-stream'},
                content=''.join('data: '+json.dumps(e)+'\n\n' for e in events).encode(),request=request)
        def client(**kw):return httpx.AsyncClient(transport=httpx.MockTransport(response),**kw)
        async def apply(payload):applied.append(payload)
        root=Path(self.tmp.name);frames={'frames':[{'uuid':'u','voiceover_text':'synthetic voiceover','fields':{}}]}
        path=root/'input.json';path.write_text(json.dumps(frames))
        async with self.sessions() as session:
            scope=await session.get(observation.ExecutionControl,execution)
            token=observation._attempt.set(observation.AttemptContext(observation._observer,scope,session,'project','n1','uncertain-attempt'))
            try:
                with patch.object(gpt_api,'_async_client',client),patch.object(settings,'gpt_max_retries',0), \
                     patch.object(settings,'gpt_api_mode','responses'),patch.object(settings,'gpt_chat_path','/codex/v1/responses'):
                    with self.assertRaisesRegex(RuntimeError,'incomplete|uncertain'):
                        await run_apply_ops_batched(project_dir=root,node_key='n1',role='assist',output_mode='project_file',
                            prompt='synthetic no-op',accompanying='',db_ctx=frames,ctx_path=path,project_id=7,
                            dense=False,footer_kind='shots_qc',force_full=True,allow_empty_ops=True,apply_fn=apply)
                self.assertEqual(len(posts),1)
                self.assertEqual(applied,[])
                async with self.sessions() as master:
                    control=await master.get(ActionExecutionControl,execution)
                    self.assertTrue(control.held,'incomplete response must durably forbid another POST')
                with self.assertRaisesRegex(RuntimeError,'held'):
                    await self.supervisor.charge_request(execution)
            finally:observation._attempt.reset(token)

    async def test_valid_noop_verified_without_fake_apply(self):
        result=await self.verify_noop()
        self.assertTrue(result['verified'],result)
        self.assertTrue(result['accepted_noop'])
        self.assertFalse(result['data_applied'])
        self.assertFalse(result['accepted_payload'])
        self.assertFalse(result['semantic_quality_verified'])

    async def test_missing_checks_cannot_verify(self):
        self.assertFalse((await self.verify_noop(missing_check=True))['verified'])

    async def test_other_current_attempt_cannot_verify(self):
        self.assertFalse((await self.verify_noop(newer_attempt=True))['verified'])

    async def test_wrong_contract_cannot_verify(self):
        self.assertFalse((await self.verify_noop(wrong_contract=True))['verified'])


if __name__ == '__main__': unittest.main()
