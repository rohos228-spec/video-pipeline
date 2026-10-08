"""Phase E: split_degraded incident, image_prompts hold explanation (read-only),
rule diary / rule display. Stubs only; no network."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.diagnosis import TITLES, derive_incidents, rule_diary_summary
from app.services.human_review_summary import build_human_review_summary

SCOPE = SimpleNamespace(execution_id='execution', project_id=2, node_keys=['split', 'ip', 'shots'],
                        observer_state='active', gaps=[])
_n = [0]


def ev(kind, *, request='r1', attempt='a1', node='split', **payload):
    _n[0] += 1
    return dict(id=_n[0], event_id=f'pe{_n[0]}', execution_id='execution', project_id=2, node_key=node,
                attempt_id=attempt, request_id=request, kind=kind, payload=payload, db_scope='project',
                created_at='2026-10-01 12:00:00')


def split_parse(**kw):
    base = dict(contract='native_split', syntax_valid=False, parsed=True, payload_present=True,
                payload_required=False, schema_valid=True, frame_count=4)
    return ev('parse_result', **{**base, **kw})


# ---------- E1: split_degraded ----------

def test_split_degraded_is_nonblocking_visible_and_not_invalid_json():
    rows = [ev('model_start'), ev('model_end', complete=True),
            split_parse(frames_source='local_voiceover_fallback', fallback_used=True, dash_rejected=True)]
    out = derive_incidents(SCOPE, rows, {})
    assert [i['type'] for i in out] == ['split_degraded']
    i = out[0]
    assert i['blocking'] is False and i['severity'] == 'warning' and i['status'] == 'human_review'
    assert i['recommendation']['automatic_action'] is False and i['frames_source'] == 'local_voiceover_fallback'
    assert i['title'] == TITLES['split_degraded'] and 'Автоповтор не выполняется' in i['fact']


@pytest.mark.parametrize('rows', [
    [ev('model_end', complete=True), split_parse(frames_source='json_replace_frames', fallback_used=False)],
    [ev('model_end', complete=True), split_parse(frames_source='dash_blocks', fallback_used=False)],
    [split_parse(frames_source='local_voiceover_fallback', fallback_used=True)],   # no complete model_end
    [ev('model_end', complete=True), split_parse()],                               # legacy fact without the field
])
def test_split_degraded_absent_on_normal_paths(rows):
    assert derive_incidents(SCOPE, rows, {}) == []


# ---------- E2: image_prompts hold explanation ----------

def _snapshot(n=3):
    return {'project_id': 2, 'frames': [{'project_id': 2, 'uuid': f'u{i}', 'number': i, 'voiceover_text': f'vo {i}',
                                         'required_prompt': True} for i in range(1, n + 1)]}


def _frames(prompts):
    return [SimpleNamespace(project_id=2, uuid=f'u{i}', number=i, voiceover_text=f'vo {i}', image_prompt=p, attrs={})
            for i, p in enumerate(prompts, 1)]


def test_verifier_lists_failing_frames_and_explanation_is_human_readable():
    from app.services.node_capabilities import explain_image_prompt_verification, verify_image_prompts
    proof = verify_image_prompts(_frames(['x' * 250, 'y' * 199, '']), project_id=2, snapshot=_snapshot())
    assert proof['status'] == 'failed' and proof['failures_total'] == 2
    assert [f['number'] for f in proof['failures']] == [2, 3]
    exp = explain_image_prompt_verification(proof)
    assert exp['criteria'] == ['missing_or_short_parent_prompt']
    assert '2, 3' in exp['summary'] and '200' in exp['summary'] and 'человек' in exp['summary']
    assert exp['hold_release'] == 'human_only' and exp['automatic_action'] is False
    assert explain_image_prompt_verification({'status': 'passed'}) is None


def test_failures_are_size_bounded():
    from app.services.node_capabilities import IMAGE_PROMPT_FAILURES_LIMIT, verify_image_prompts
    proof = verify_image_prompts(_frames([''] * 60), project_id=2, snapshot=_snapshot(60))
    assert len(proof['failures']) == IMAGE_PROMPT_FAILURES_LIMIT and proof['failures_total'] == 60


def test_unknown_and_integrity_failures_are_explained():
    from app.services.node_capabilities import explain_image_prompt_verification, verify_image_prompts
    unknown = verify_image_prompts(_frames(['x' * 250]), project_id=2, snapshot=None)
    assert unknown['status'] == 'unknown' and 'не смогла сравнить' in explain_image_prompt_verification(unknown)['summary']
    changed = verify_image_prompts(_frames(['x' * 250, 'x' * 250]), project_id=2, snapshot=_snapshot(3))
    exp = explain_image_prompt_verification(changed)
    assert 'frame_set_changed' in exp['criteria'] and 'Набор кадров изменился' in exp['summary']


def test_diagnosis_emits_verification_hold_with_reason_and_no_auto_action():
    from app.services.node_capabilities import verify_image_prompts
    proof = verify_image_prompts(_frames(['x' * 250, 'y' * 199, '']), project_id=2, snapshot=_snapshot())
    rows = [ev('node_result_verification', node='ip', **proof), ev('node_error', node='ip', error_type='RuntimeError')]
    out = {i['type']: i for i in derive_incidents(SCOPE, rows, {})}
    hold = out['verification_hold']
    assert hold['blocking'] is True and hold['status'] == 'human_review'
    assert hold['recommendation']['automatic_action'] is False
    assert hold['hold_explanation']['failing_frames'][0]['number'] == 2
    assert '2, 3' in hold['fact']
    ok = verify_image_prompts(_frames(['x' * 250] * 3), project_id=2, snapshot=_snapshot())
    assert 'verification_hold' not in {i['type'] for i in derive_incidents(SCOPE, [ev('node_result_verification', node='ip', **ok)], {})}


def test_recompute_is_read_only_and_does_not_touch_hold(tmp_path):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import select, func
    from app.models import Base, Frame
    from app.services.node_capabilities import recompute_image_prompt_verification

    async def go():
        engine = create_async_engine('sqlite+aiosqlite:///' + str(tmp_path / 'p.db'))
        async with engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        from sqlalchemy.ext.asyncio import async_sessionmaker
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            for i, p in enumerate(['x' * 250, 'short'], 1):
                s.add(Frame(project_id=2, number=i, voiceover_text=f'vo {i}', image_prompt=p, uuid=f'u{i}', attrs={}))
            await s.commit()
        with patch('app.services.actions_v1.ActionSupervisor.set_held', new_callable=AsyncMock) as held:
            out = await recompute_image_prompt_verification(engine, project_id=2, snapshot=_snapshot(2))
        async with async_sessionmaker(engine)() as s:
            rows = (await s.execute(select(Frame.number, Frame.image_prompt).order_by(Frame.number))).all()
        await engine.dispose()
        return out, held, rows

    out, held, rows = asyncio.run(go())
    assert out['proof']['status'] == 'failed' and out['explanation']['failing_frames'][0]['number'] == 2
    held.assert_not_called()
    assert rows == [(1, 'x' * 250), (2, 'short')]


def test_capability_data_for_hold_stays_bounded():
    """W5 D05: image_prompts may take one retry for a typed format incident; the hold itself (a failed or unknown
    post-write check, verification_hold) is never retried: that stays a human decision (see actions_recovery gates)."""
    from app.services.node_capabilities import capability_for
    from app.services.diagnosis import INCIDENT_OUTCOMES
    cap = capability_for('image_prompts')
    assert set(cap.actions) == {'read_events', 'read_contract', 'wait', 'human_review', 'retry_same', 'retry_profile', 'finish'}
    assert cap.contract()['semantic_quality_verified'] is False
    assert INCIDENT_OUTCOMES['verification_hold']['paid_retry'] == 'never'
    assert INCIDENT_OUTCOMES['verification_hold'].get('recovery_class') is None


# ---------- E3: rule diary ----------

def test_rule_diary_payload_is_bounded_and_reads_non_passstop_rule():
    from app.services.apply_ops_batches import PassStopError, rule_diary_payload
    entries = [{'kind': 'fix', 'rule': f'R-X{i % 30}'} for i in range(500)] + [{'kind': 'warn', 'rule': 'R-UUID'}]
    p = rule_diary_payload(entries, call=3, level=1)
    assert p['counts'] == {'fix': 500, 'warn': 1} and len(p['rules']) <= 12 and p['stop_rule'] is None and 'note' not in p
    exc = PassStopError('x' * 1000, rule='R-SHOT-COVERAGE', diary=[])
    p = rule_diary_payload([], call=1, level=2, stop_exc=exc)
    assert p['stop_rule'] == 'R-SHOT-COVERAGE' and len(p['note']) == 200
    other = RuntimeError('boom'); other.rule = 'R-LAYOUT'
    assert rule_diary_payload([], call=1, level=1, stop_exc=other)['stop_rule'] == 'R-LAYOUT'
    bad = RuntimeError('boom'); bad.rule = 'not a rule id'
    assert rule_diary_payload([], call=1, level=1, stop_exc=bad)['stop_rule'] is None


def test_emit_rule_diary_is_best_effort_and_skips_clean_calls():
    from app.services import apply_ops_batches as aob
    with patch('app.services.observation.record_fact', new_callable=AsyncMock) as fact:
        asyncio.run(aob._emit_rule_diary([], call=1, level=1))
        fact.assert_not_called()
        asyncio.run(aob._emit_rule_diary([{'kind': 'fix', 'rule': 'R-UUID'}], call=1, level=1))
        assert fact.call_args.args[0] == 'rule_diary'
    with patch('app.services.observation.record_fact', new_callable=AsyncMock, side_effect=RuntimeError('disk')):
        asyncio.run(aob._emit_rule_diary([{'kind': 'fix', 'rule': 'R-UUID'}], call=1, level=1))  # must not raise


def test_diary_summary_merges_and_ignores_garbage():
    rows = [ev('rule_diary', counts={'fix': 2}, rules={'R-UUID': 2}, stop_rule=None),
            ev('rule_diary', counts={'fix': 1, 'stop': 1}, rules={'R-UUID': 1, 'R-SHOT-COVERAGE': 1}, stop_rule='R-SHOT-COVERAGE'),
            ev('rule_diary', counts='bad', rules=None)]
    s = rule_diary_summary(rows)
    assert s['counts'] == {'fix': 3, 'stop': 1} and s['rules']['R-UUID'] == 3 and s['stop_rule'] == 'R-SHOT-COVERAGE' and s['calls'] == 3
    assert rule_diary_summary([]) is None


def test_node_error_with_passstop_rule_shows_rule_title_action_and_diary():
    rows = [ev('rule_diary', node='shots', counts={'fix': 3, 'stop': 1}, rules={'R-UUID': 3, 'R-SHOT-COVERAGE': 1}, stop_rule='R-SHOT-COVERAGE'),
            ev('node_error', node='shots', error_type='PassStopError', rule='R-SHOT-COVERAGE')]
    inc = derive_incidents(SCOPE, rows, {})
    assert len(inc) == 1 and inc[0]['type'] == 'contract_rejected' and inc[0]['rule'] == 'R-SHOT-COVERAGE'
    assert 'Кадры иллюстрируют закадр' in inc[0]['fact'] and 'предупреждает' in inc[0]['fact'] and 'R-UUID×3' in inc[0]['fact']
    assert inc[0]['rule_diary']['rules']['R-UUID'] == 3


def test_node_error_without_rule_field_takes_rule_from_diary_fact():
    rows = [ev('rule_diary', node='shots', counts={'stop': 1}, rules={'R-LAYOUT': 1}, stop_rule='R-LAYOUT'),
            ev('node_error', node='shots', error_type='RuntimeError')]
    inc = derive_incidents(SCOPE, rows, {})
    assert [(i['type'], i.get('rule')) for i in inc] == [('contract_rejected', 'R-LAYOUT')]
    plain = derive_incidents(SCOPE, [ev('node_error', node='shots', error_type='RuntimeError')], {})
    assert plain[0].get('rule') is None and 'точная причина неизвестна' in plain[0]['fact']


def test_human_review_summary_shows_rule_hold_reason_and_source():
    s = build_human_review_summary(node_key='shots', incident_id='i', incident_type='contract_rejected',
        incident_details={'event_ids': ['e'], 'rule': 'R-SHOT-COVERAGE', 'rule_diary': {'rules': {'R-UUID': 3}}},
        escalation_reason='x', model_steps_used=0, max_model_steps=3)
    assert 'R-SHOT-COVERAGE' in s.text and 'R-UUID x3' in s.text and s.rule == 'R-SHOT-COVERAGE'
    h = build_human_review_summary(node_key='ip', incident_id='i', incident_type='verification_hold',
        incident_details={'hold_explanation': {'summary': 'У 2 кадров промпт короче 200 символов.',
                                               'failing_frames': [{'number': 2}]}},
        escalation_reason='x', model_steps_used=0, max_model_steps=3)
    assert 'короче 200' in h.text and h.hold_failing_frames[0]['number'] == 2 and h.as_dict()['hold_reason']
    d = build_human_review_summary(node_key='split', incident_id='i', incident_type='split_degraded',
        incident_details={'frames_source': 'local_voiceover_fallback'}, escalation_reason='x',
        model_steps_used=0, max_model_steps=3)
    assert 'local_voiceover_fallback' in d.text and d.incident_title == TITLES['split_degraded']
