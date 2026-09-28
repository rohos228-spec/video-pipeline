"""Safety faults against a copy of a genuinely exhausted worker checkpoint.

Each container retains the original evidence and appends an explicit test-only
operator fault. The actual worker stays alive; no successful statuses are set.
"""
import asyncio
import json
import uuid
from datetime import datetime
from sqlalchemy import select, func


async def run(mode, client, fixtures, observer, root, result):
    from app.models import Project, WorkflowRun, NodeRun
    from app.db import SessionLocal
    from app.project_db import project_db_session_scope
    from app.services.observation import ExecutionControl, ObservationEvent
    from app.services.actions_v1 import ActionRecord
    from app.services.actions_recovery import RecoveryAction
    from app.services.diagnosis import Incident, get_diagnoser
    base = 'http://127.0.0.1:18765/api/projects/2'
    async with SessionLocal() as session:
        scope = await session.scalar(select(ExecutionControl).where(ExecutionControl.project_id == 2))
        execution_id = scope.execution_id
        source = await session.scalar(select(Incident).where(Incident.execution_id == execution_id,
            Incident.node_key == fixtures.NODES[0], Incident.type.in_(['invalid_json','missing_payload']))
            .order_by(Incident.detected_at.desc()))
        assert source is not None
        before = await session.scalar(select(func.count()).select_from(ObservationEvent).where(
            ObservationEvent.execution_id == execution_id, ObservationEvent.kind == 'node_start'))
        node_key = fixtures.NODES[0]
        incident_id = source.incident_id
        if mode == 'deny-user-stop':
            project = await session.get(Project, 2)
            project.meta = {**project.meta, 'user_stop': True}
        elif mode == 'deny-uncertain':
            session.add(ObservationEvent(event_id=str(uuid.uuid4()), execution_id=execution_id,
                project_id=2, workflow_run_id=scope.workflow_run_id, node_key=node_key,
                attempt_id=source.attempt_id, request_id='synthetic-uncertain-request', db_scope='master',
                kind='model_start', payload={'synthetic_fault':True, 'input_revision':'test-only-no-POST'},
                created_at=datetime.utcnow()))
        elif mode == 'deny-graph':
            run = await session.get(WorkflowRun, scope.workflow_run_id)
            run.edges_snapshot = [*(run.edges_snapshot or []), {'source':node_key,'target':'synthetic-stale-graph'}]
        elif mode == 'deny-config':
            async with project_db_session_scope(2) as replica:
                project = await replica.get(Project,2)
                project.gpt_text_overrides = {**(project.gpt_text_overrides or {}), 'enrich_1':'SYNTHETIC CONFIG FAULT'}
        elif mode == 'deny-scope':
            node_key = 'n_script'
            incident_id = str(uuid.uuid4())
            session.add(Incident(incident_id=incident_id, execution_id=execution_id, project_id=2,
                node_key=node_key, attempt_id=source.attempt_id, type='missing_payload', severity='error',
                status='human_review', details={**source.details,'synthetic_fault':True,'currently_relevant':True}))
        else:
            raise AssertionError('unknown synthetic fault')
        session.add(ActionRecord(action_id=str(uuid.uuid4()), execution_id=execution_id, project_id=2,
            action_kind='synthetic_fault_injection', node_key=node_key, initiator='offline_test_runner',
            rationale=mode+'; source evidence is copied, not a new provider request',
            idempotency_key='fault:'+mode, status='accepted', preconditions={'synthetic_only':True},
            evidence_ids=[], verification={}))
        await session.commit()
    await observer.catch_up()
    if mode != 'deny-scope':
        await get_diagnoser(observer).update(execution_id)
    response = await client.post(base+f'/observations/{execution_id}/actions/retry', json={
        'node_key':node_key,'incident_id':incident_id,'rationale':'synthetic safety denial '+mode,
        'idempotency_key':'denial:'+mode})
    assert response.status_code == 409, response.text
    expected = {'deny-user-stop':'user_stop','deny-uncertain':'uncertain','deny-graph':'graph revision',
                'deny-config':'input/config','deny-scope':'outside recovery scope'}[mode]
    assert expected in response.text, response.text
    await asyncio.sleep(5)  # Actual scheduler/worker has several polling opportunities.
    async with SessionLocal() as session:
        after = await session.scalar(select(func.count()).select_from(ObservationEvent).where(
            ObservationEvent.execution_id == execution_id, ObservationEvent.kind == 'node_start'))
        assert before == after, (before,after)
        assert await session.scalar(select(func.count()).select_from(RecoveryAction)) == 0
        nodes = (await session.scalars(select(NodeRun).where(NodeRun.workflow_run_id == scope.workflow_run_id))).all()
    assert not fixtures.FixtureHandler.counts, fixtures.FixtureHandler.counts
    result.update(accepted=True, execution_id=execution_id, safety_denial=mode,
        reason=response.json()['detail'], new_worker_attempts=after-before, model_posts=0,
        source_checkpoint='copied exhausted-checkpoint: native worker exhausted 9 failures, 18 synthetic POSTs; see checkpoint provenance.json',
        interventions=[{'initiator':'offline_test_runner','kind':'synthetic_fault_injection','mode':mode}],
        node_states={n.node_key:n.status.value for n in nodes})
    (root/'denial-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
