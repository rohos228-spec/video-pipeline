"""Saved journal rules and isolated persistence; no provider or worker required."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from sqlalchemy import select, text
from app.services.diagnosis import derive_incidents, Diagnoser, DiagnosisState, Incident
from app.services.observation import ObservationEvent
import test_journal

SCOPE = SimpleNamespace(execution_id='execution', project_id=2, node_keys=['script','check'], observer_state='active', gaps=[])

def event(kind, *, request='r1', attempt='a1', node='script', project=2, **payload):
    event.counter += 1
    return dict(id=event.counter,event_id=f'e{event.counter}',execution_id='execution',project_id=project,
        node_key=node,attempt_id=attempt,request_id=request,kind=kind,payload=payload,db_scope='project',created_at='2026-09-28 12:00:00')
event.counter=0

def bad(**kw):
    return event('parse_result',payload_required=True,parsed=False,syntax_valid=False,payload_present=False,**kw)

def recovery():
    return [event('json_retry',request='r1',source_request_id='r1'),
        event('model_start',request='r2',previous_request_id='r1'),
        event('parse_result',request='r2',payload_required=True,parsed=True,payload_present=True,schema_valid=None),
        event('data_applied',request='r2',transactional=True,result={'ok':True}),
        event('node_change',request=None,changes={'status':{'after':['done']}},transactional=True)]

class Rules(unittest.TestCase):
    def test_format_retry_requires_accepted_response_and_same_attempt_commit(self):
        original=bad(); result=derive_incidents(SCOPE,[original]+recovery(),{})
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['status'],'resolved_confirmed')
        self.assertEqual(result[0]['resolution']['kind'],'format_recovery')
        self.assertIn(original['event_id'],result[0]['event_ids'])

    def test_http_or_parse_or_other_attempt_does_not_close(self):
        original=bad()
        for evidence in ([event('model_end',complete=True)], recovery()[:3],
                         [{**e,'attempt_id':'other'} for e in recovery()]):
            self.assertNotEqual(derive_incidents(SCOPE,[original]+evidence,{})[0]['status'],'resolved_confirmed')

    def test_late_delivery_and_identical_text_in_different_attempts(self):
        rows=[bad()]+recovery()
        self.assertEqual(derive_incidents(SCOPE,list(reversed(rows)),{})[0]['status'],'resolved_confirmed')
        self.assertEqual(len(derive_incidents(SCOPE,[bad(),bad(attempt='a2')],{})),2)

    def test_bad_retry_response_is_one_incident_not_two(self):
        rows=[bad(),event('json_retry',source_request_id='r1'),event('model_start',request='r2',previous_request_id='r1'),bad(request='r2')]
        result=derive_incidents(SCOPE,rows,{})
        self.assertEqual(len(result),1)
        self.assertNotEqual(result[0]['status'],'resolved_confirmed')

    def test_optional_text_empty_ops_unknown_schema_and_repairs_not_failures(self):
        rows=[event('parse_result',node='check',payload_required=False,parsed=False,payload_present=False,ops_count=0,schema_valid=None),
            event('domain_check',node='check',verdict='pass'),event('runtime_check',check='write_contract',status='filtered',input_count=1,output_count=1),
            event('runtime_check',check='json_text_repair',status='performed',changed=True)]
        self.assertEqual(derive_incidents(SCOPE,rows,{}),[])

    def test_required_empty_payload_and_explicit_gate_failure(self):
        rows=[event('parse_result',payload_required=True,parsed=True,payload_present=False,ops_count=0),
            event('runtime_check',check='frame_coverage_gate',status='failed',performed=True,missing=1)]
        self.assertEqual({i['type'] for i in derive_incidents(SCOPE,rows,{})},{'missing_payload','contract_rejected'})

    def test_wait_and_stop_are_not_errors_or_blind_retry(self):
        rows=[event('runtime_wait',reason='queue_predecessor',initiator='worker'),event('project_control',node=None,user_stop=True)]
        result=derive_incidents(SCOPE,rows,{'nodes':{'script':{'status':'queued'}},'user_stop':True})
        self.assertTrue(all(not i['blocking'] and i['severity']=='info' for i in result))
        self.assertTrue(all(i['recommendation']['action']=='wait' for i in result))
        self.assertFalse(any(i['recommendation']['automatic_action'] for i in result))

    def test_uncertain_is_not_inferred_from_age_or_status(self):
        start=event('model_start')
        self.assertEqual(derive_incidents(SCOPE,[start],{'nodes':{'script':{'status':'running'}}}),[])
        result=derive_incidents(SCOPE,[start,event('node_error',request=None,error_type='CancelledError')],{})
        self.assertTrue(any(i['type']=='uncertain_request' and i['recommendation']['action']=='review_provider_response' for i in result))

    def test_db_disagreement_and_gap_are_separate_and_incomplete(self):
        result=derive_incidents(SCOPE,[bad()]+recovery(),{'disagreement':True,'gaps':[{'kind':'observer_error'}]})
        self.assertEqual(len(result),3)
        self.assertTrue(any(i['type']=='journal_gap' and i['evidence_incomplete'] for i in result))
        self.assertTrue(any(i['type']=='db_disagreement' and i['status']!='resolved_confirmed' for i in result))

    def test_other_project_and_nodes_excluded(self):
        self.assertEqual(derive_incidents(SCOPE,[bad(project=1),bad(node='video')],{}),[])

    def test_catalog_transport_and_uncertain_no_blind_retry(self):
        result=derive_incidents(SCOPE,[event('model_error',error_code='gpt_auth',uncertain=False)],{})
        self.assertEqual(result[0]['catalog_code'],'gpt_auth')
        self.assertEqual(result[0]['status'],'human_review')
        result=derive_incidents(SCOPE,[event('model_error',uncertain=True,error_code='gpt_timeout')],{})
        self.assertEqual(result[0]['recommendation']['action'],'review_provider_response')

    def test_saved_normal_and_malformed_full_history(self):
        summaries={}
        for mode in ('normal','malformed'):
            data=json.loads((Path('/observe-history')/(mode+'.json')).read_text())
            events=data['events']; scope=SimpleNamespace(execution_id=events[0]['execution_id'],project_id=2,
                node_keys=sorted({e['node_key'] for e in events if e['node_key']}),observer_state='stopped',gaps=[])
            result=derive_incidents(scope,events,{})
            summaries[mode]=result
            if mode=='normal': self.assertFalse(any(i['blocking'] for i in result))
            else:
                self.assertEqual(len(result),1)
                self.assertEqual(result[0]['status'],'resolved_confirmed')
        Path('/results/saved-history-diagnoses.json').write_text(json.dumps(summaries,ensure_ascii=False,indent=2))

class Persistence(test_journal.JournalAcceptance):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.service=Diagnoser(self.observer)

    async def insert(self, rows):
        async with self.master() as s:
            for row in rows:
                s.add(ObservationEvent(**{**row,'id':None,'execution_id':self.scope.execution_id,'workflow_run_id':7,
                    'created_at':__import__('datetime').datetime(2026,9,28,12)}))
            await s.commit()

    async def test_long_history_restart_late_recovery_and_idempotence(self):
        original=bad(); await self.insert([original]+[event('node_output',request=None) for _ in range(620)])
        await self.service.update(self.scope.execution_id,snapshot={})
        async with self.master() as s:
            first=(await s.scalars(select(Incident))).one(); identifier=first.incident_id
            state=await s.get(DiagnosisState,self.scope.execution_id); self.assertGreater(state.cursor,500)
        restarted=Diagnoser(self.observer)
        await restarted.update(self.scope.execution_id,snapshot={})
        await self.insert(recovery()); await restarted.update(self.scope.execution_id,snapshot={})
        await restarted.update(self.scope.execution_id,snapshot={})
        async with self.master() as s:
            rows=(await s.scalars(select(Incident))).all()
            self.assertEqual(len(rows),1); self.assertEqual(rows[0].incident_id,identifier)
            self.assertEqual(rows[0].status,'resolved_confirmed')

    async def test_diagnosis_failure_cannot_touch_business_or_replay(self):
        await self.insert([bad()])
        with patch('app.services.diagnosis.derive_incidents',side_effect=RuntimeError('synthetic diagnosis failure')):
            await self.service.safe_update(self.scope.execution_id)
        self.assertIn(self.scope.execution_id,self.service.failures)
        async with self.master() as s:
            self.assertIsNone(await s.get(DiagnosisState,self.scope.execution_id))
        await self.service.update(self.scope.execution_id,snapshot={})
        async with self.master() as s: self.assertEqual(len((await s.scalars(select(Incident))).all()),1)

    async def test_read_only_api_scope_unavailable_and_restart(self):
        import httpx
        from fastapi import FastAPI
        from app.services import observation
        from app.web.routers.observations import router
        previous=observation._observer;observation._observer=self.observer
        self.observer.diagnoser=self.service
        app=FastAPI();app.include_router(router,prefix='/api')
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://localhost') as client:
                url=f'/api/projects/2/observations/{self.scope.execution_id}/diagnoses'
                self.assertFalse((await client.get(url)).json()['available'])
                await self.insert([bad()]+recovery()); await self.service.update(self.scope.execution_id,snapshot={})
                first=(await client.get(url)).json()
                self.assertEqual(first['incidents'][0]['status'],'resolved_confirmed')
                self.assertFalse(first['automatic_actions'])
                self.assertEqual((await client.get(url.replace('/projects/2/','/projects/1/'))).status_code,404)
                self.observer.diagnoser=Diagnoser(self.observer)
                again=(await client.get(url)).json()
                self.assertEqual(first['incidents'],again['incidents'])
                self.assertEqual(first['cursor'],again['cursor'])
                self.observer.diagnoser.failures[self.scope.execution_id]='OSError'
                self.assertFalse((await client.get(url)).json()['available'])
        finally: observation._observer=previous

if __name__=='__main__': unittest.main(verbosity=2)
