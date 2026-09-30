"""Capabilities of existing registry nodes, not a new executor or workflow."""
from dataclasses import asdict, dataclass


READ_ACTIONS = ("read_events", "read_contract", "wait", "human_review")


@dataclass(frozen=True)
class NodeCapability:
    node_type: str
    result_kind: str
    executor: str
    phases: tuple[str, ...]
    effects: tuple[str, ...]
    actions: tuple[str, ...]
    managed: bool = False
    approval: tuple[str, ...] = ("scope", "budget", "provider_profile")

    def contract(self):
        return {**asdict(self), "semantic_quality_verified": False,
                "write_authority": "existing worker only",
                "arbitrary_shell_code_paths": False}


_JOB_PHASES = ("reserved", "submitted", "queued", "running", "completed",
               "downloading", "verifying", "verified", "failed", "cancelled", "unknown")
_CAPABILITIES = {
    'plan': NodeCapability('plan','project_text','make_plan.run → xlsx_step_runners.run_plan_xlsx',
        ('running','parse','validate','apply','commit','human_approval'),
        ('project_database','local_xlsx','hitl_request'),READ_ACTIONS,True),
    'script': NodeCapability('script','project_text','make_script.run → xlsx_step_runners.run_script_xlsx',
        ('running','parse','validate','apply','commit','human_approval'),
        ('project_database','local_xlsx','hitl_request'),READ_ACTIONS,True),
    'split': NodeCapability('split','frame_set','split_frames.run → xlsx_step_runners.run_split_xlsx',
        ('running','parse','apply','commit','verify'),('project_database','local_xlsx'),READ_ACTIONS,True),
    'image_prompts': NodeCapability('image_prompts','frame_prompts',
        'generate_image_prompts.run → xlsx_step_runners.run_img_pr_xlsx',
        ('running','parse','validate','apply','commit','verify'),
        ('project_database','local_xlsx','local_checkpoint'),READ_ACTIONS,True),
    "excel_gpt": NodeCapability("excel_gpt", "structured", "enrich_xlsx.run",
        ("running", "parse", "validate", "apply", "commit"), ("project_database",),
        READ_ACTIONS + ("retry_same", "retry_profile", "finish"), True),
    "images": NodeCapability("images", "image", "generate_images.run → kie_http",
        _JOB_PHASES, ("provider_job", "local_file", "artifact_row"), READ_ACTIONS, True),
    "videos": NodeCapability("videos", "video", "generate_videos.run → kie_http",
        _JOB_PHASES, ("provider_job", "local_file", "artifact_row"), READ_ACTIONS, False),
    "audio": NodeCapability("audio", "audio", "generate_audio.run → elevenlabs_api",
        ("reserved", "submitted", "verifying", "verified", "alignment", "failed", "unknown"),
        ("provider_generation", "local_file", "frame_timing", "artifact_row"), READ_ACTIONS, False),
    "assemble": NodeCapability("assemble", "assembly", "assemble.run → assembly.assemble",
        ("inputs", "processing", "subtitles", "verifying", "verified", "failed", "unknown"),
        ("local_file", "artifact_row"), READ_ACTIONS, False),
    "publish": NodeCapability("publish", "publication", "publish.run",
        ("input_validation", "dry_run", "blocked"), ("external_accounts",),
        ("read_events", "read_contract", "human_review"), False),
}


def capability_for(node_type: str) -> NodeCapability:
    from app.orchestrator.node_registry import is_work_node_type
    if node_type in _CAPABILITIES and is_work_node_type(node_type):
        return _CAPABILITIES[node_type]
    return NodeCapability(node_type, "unsupported", "", ("unknown",), (),
                          ("read_events", "read_contract", "human_review"))


def require_managed_capability(node_type: str):
    capability = capability_for(node_type)
    if not capability.managed:
        raise ValueError("node adapter has no accepted managed executor")
    return capability


def image_prompts_contract():
    """Original img_pr parent/child and write rules, without recovery authority."""
    from app.services.img_pr_batches import IMG_PR_MIN_WRITE_CHARS
    return {'node_type':'image_prompts','input':'original scoped Frame rows and resolved native prompt',
        'output':'same scoped rows; primary image_prompt and native second-shot attrs',
        'min_characters':IMG_PR_MIN_WRITE_CHARS,'primary_prompt_required':True,
        'threshold':'either native prompt slot; short primary allowed with complete second slot',
        'target_predicate':'vo_shot_expand.is_img_pr_vo_parent; children exempt from parent threshold',
        'allowed_fields':['image_prompt','image_prompt_shot2','characters'],
        'write_contract':'node_write_contract.filter_ops_for_node(node_kind=img_pr)',
        'native_partial_extraction_preserved':True,'semantic_quality_verified':False,
        'arbitrary_shell_code_paths':False}


def image_prompt_input_snapshot(frames,*,project_id):
    from app.services.vo_shot_expand import is_img_pr_vo_parent
    return {'project_id':project_id,'frames':[
        {'project_id':f.project_id,'uuid':f.uuid,'number':f.number,
         'voiceover_text':f.voiceover_text,'required_prompt':is_img_pr_vo_parent(f)}
        for f in frames]}


def verify_image_prompts(frames,*,project_id,snapshot):
    """Read-only native output integrity; no semantic/model quality claim."""
    from app.services.img_pr_batches import IMG_PR_MIN_WRITE_CHARS
    from app.services.plan_shot2 import SHOT2_PROMPT_ATTR
    from app.services.observation import digest
    rows=[{'project_id':f.project_id,'uuid':f.uuid,'number':f.number,
        'voiceover_text':f.voiceover_text,'image_prompt':f.image_prompt,
        'second_prompt':(f.attrs or {}).get(SHOT2_PROMPT_ATTR)} for f in frames]
    proof={'node_type':'image_prompts','frame_count':len(rows),'frame_set_sha256':digest(rows),
        'input_sha256':digest(snapshot) if snapshot is not None else None,
        'business_commit_claim':False,'semantic_quality_verified':False,
        'source':'original executor Frame rows; committed outbox proves application'}
    if (not isinstance(snapshot,dict) or not isinstance(snapshot.get('frames'),list)
            or not snapshot['frames'] or any(not isinstance(r,dict) or
                not {'uuid','number','voiceover_text','required_prompt'}<=r.keys() for r in snapshot['frames'])):
        return {**proof,'status':'unknown','reason':'captured input unavailable or incomplete','errors':[],
            'voiceover_preserved':False}
    errors=[];before=snapshot['frames']
    if snapshot.get('project_id')!=project_id or any(r.get('project_id')!=project_id for r in before):
        errors.append('input_project_outside_scope')
    before_ids=[r['uuid'] for r in before];after_ids=[r['uuid'] for r in rows]
    if any(not uid for uid in before_ids+after_ids):errors.append('missing_uuid')
    if len(set(before_ids))!=len(before_ids) or len(set(after_ids))!=len(after_ids):errors.append('duplicate_uuid')
    if set(before_ids)!=set(after_ids) or len(before)!=len(rows):errors.append('frame_set_changed')
    if any(r['project_id']!=project_id for r in rows):errors.append('output_project_outside_scope')
    if len({r['number'] for r in rows})!=len(rows):errors.append('duplicate_number')
    by_id={r['uuid']:r for r in rows};voiceover_preserved=True;targets=0
    for row in before:
        current=by_id.get(row['uuid'])
        if current is None:
            voiceover_preserved=False
            continue
        if current['voiceover_text']!=row['voiceover_text']:
            voiceover_preserved=False;errors.append('voiceover_changed:'+str(row['uuid']))
        if current['number']!=row['number']:errors.append('frame_number_changed:'+str(row['uuid']))
        if row['required_prompt'] is True:
            targets+=1
            primary=str(current['image_prompt'] or '').strip()
            second=str(current['second_prompt'] or '').strip()
            if not primary or max(len(primary),len(second))<IMG_PR_MIN_WRITE_CHARS:
                errors.append('missing_or_short_parent_prompt:'+str(row['uuid']))
        elif row['required_prompt'] is not False:errors.append('input_target_rule_unknown')
    if not targets:errors.append('no_required_parent_frames')
    return {**proof,'status':'failed' if errors else 'passed',
        'reason':errors[0] if errors else None,'errors':errors,
        'voiceover_preserved':voiceover_preserved,'target_frame_count':targets}


async def record_image_prompt_input(frames,project):
    from app.services.observation import _attempt,record_fact,digest
    ctx=_attempt.get()
    if not ctx:return
    snapshot=image_prompt_input_snapshot(frames,project_id=project.id)
    ctx.image_prompt_input=snapshot
    await record_fact('node_input',{'contract':'native_image_prompts','frame_count':len(frames),
        'input_sha256':digest(snapshot)},body=snapshot)


async def record_image_prompt_parse(reply,ops,*,batch_i,target_uuids):
    import json
    from app.services.img_pr_batches import writeable_img_pr_ops,uuid_of_op
    from app.services.observation import _attempt,record_fact,digest,completed_model_request_id
    ctx=_attempt.get()
    if not ctx:return
    try:json.loads(reply);syntax=True
    except (ValueError,TypeError):syntax=False
    writable=writeable_img_pr_ops(ops);ids=[uuid_of_op(op) for op in writable]
    scoped=bool(ids) and len(set(ids))==len(ids) and set(ids)<=set(target_uuids)
    request_id=completed_model_request_id()
    requests=getattr(ctx,'image_prompt_batch_requests',None)
    if requests is None:requests={};ctx.image_prompt_batch_requests=requests
    requests[batch_i]=request_id
    if request_id is None and ctx.scope.mode=='managed':
        ctx.observer.mark_gap(ctx.scope.execution_id,RuntimeError('native image prompt model request identity unavailable'))
    await record_fact('parse_result',{'contract':'native_image_prompts','batch':batch_i,
        'syntax_valid':syntax,'parsed':bool(ops),'schema_valid':scoped,'ops_count':len(ops),
        'batch_coverage_complete':set(ids)==set(target_uuids),
        'writeable_ops_count':len(writable),'payload_required':False,
        'parser':'original img_pr_batches.parse_img_pr_ops; partial extraction preserved',
        'sha256':digest(ops)},body={'text':reply,'extracted_ops':ops},request_id=request_id)
    await record_fact('runtime_check',{'check':'native_image_prompt_batch','batch':batch_i,
        'status':'passed' if scoped else 'failed','performed':True,
        'business_commit_claim':False},request_id=request_id)


async def observe_image_prompt_result(session,project,node_type):
    from sqlalchemy import select
    from app.models import Frame
    from app.services.observation import _attempt,record_fact
    ctx=_attempt.get()
    if not ctx or ctx.scope.mode!='managed' or node_type!='image_prompts':return
    frames=(await session.scalars(select(Frame).where(Frame.project_id==project.id).order_by(Frame.number)
        .execution_options(populate_existing=True))).all()
    proof=verify_image_prompts(frames,project_id=project.id,snapshot=getattr(ctx,'image_prompt_input',None))
    output={'project_id':project.id,'frames':[
        {'uuid':f.uuid,'number':f.number,'voiceover_text':f.voiceover_text,
            'image_prompt':f.image_prompt,'attrs':f.attrs} for f in frames]}
    await record_fact('node_output',{'frame_count':len(frames),
        'frame_set_sha256':proof['frame_set_sha256'],'source':'original executor project.db rows'},body=output)
    await record_fact('node_result_verification',proof,body=proof)
    if proof['status']!='passed':
        from app.services.actions_v1 import ActionSupervisor
        await ActionSupervisor(ctx.observer).set_held(project_id=project.id,execution_id=ctx.scope.execution_id,
            held=True,initiator='native_image_prompt_verifier',rationale=proof['reason'],
            idempotency_key='image-prompts-verify:'+ctx.attempt_id,operator_request=False)
        raise RuntimeError('managed image prompt verification '+proof['status'])


def project_text_contract(node_type):
    """Exact native contracts; no generic write/retry permission for text nodes."""
    from app.services.plan_validation import MIN_GENERAL_PLAN_CHARS
    if node_type not in {'plan','script'}:raise ValueError('unsupported project text contract')
    return {'node_type':node_type,'input_field':'topic' if node_type=='plan' else 'general_plan',
        'output_field':'general_plan' if node_type=='plan' else 'script_text',
        'apply_ops_field':'общий_план' if node_type=='plan' else 'закадровый_текст',
        'min_characters':MIN_GENERAL_PLAN_CHARS if node_type=='plan' else 200,
        'formats':['apply_ops_json'] if node_type=='plan' else ['apply_ops_json','VOICEOVER_block','plain_text_native_fallback'],
        'validator':'plan_validation.is_meaningful_general_plan' if node_type=='plan' else 'make_script.run minimum voiceover length',
        'human_approval_preserved':True,'semantic_quality_verified':False,
        'retry_policy':'original native runtime only; no supervisor recovery/profile for this family'}


def project_text_parse_evidence(reply, text, node_type):
    """Report actual extraction and native validation, without requiring JSON for VO."""
    import json
    from types import SimpleNamespace
    from app.services import db_apply

    contract = project_text_contract(node_type)
    try:
        json.loads(reply)
        syntax_valid = True
    except (ValueError, TypeError):
        syntax_valid = False
    parsed = db_apply.extract_apply_ops_json(reply or '')
    proof = verify_project_text(SimpleNamespace(**{contract['output_field']: text}), node_type)
    return {
        'contract': 'native_' + node_type,
        'node_type': node_type,
        'syntax_valid': syntax_valid,
        'parsed': isinstance(parsed, dict) or (node_type == 'script' and bool(text)),
        'payload_present': bool(text),
        'payload_required': node_type == 'plan',
        'nonempty_payload_required': True,
        'schema_valid': proof['status'] == 'passed',
        'schema_note': 'Original field extraction and native validation; no semantic quality claim',
        'output_field': contract['output_field'],
        'characters': proof['characters'],
        'sha256': proof['sha256'],
        'semantic_quality_verified': False,
    }


async def record_project_text_input(project, node_type):
    from app.services.observation import _attempt, record_fact
    if _attempt.get() is None:
        return
    contract = project_text_contract(node_type)
    await record_fact('node_input', {'contract': 'native_' + node_type,
        'source': 'authoritative project.db fields'},
        body={contract['input_field']: getattr(project, contract['input_field'], None)})


async def record_project_text_parse(reply, text, node_type):
    from app.services.observation import _attempt, record_fact
    ctx = _attempt.get()
    if ctx is None:
        return
    proof = project_text_parse_evidence(reply, text, node_type)
    await record_fact('parse_result', proof, request_id=ctx.last_request_id,
        body={'reply': reply, 'extracted_text': text})
    await record_fact('runtime_check', {'check': 'native_' + node_type + '_text_requirement',
        'performed': True, 'status': 'passed' if proof['schema_valid'] else 'failed',
        'initiator': 'original executor', 'business_commit_claim': False,
        'semantic_quality_verified': False}, request_id=ctx.last_request_id)


def verify_project_text(project,node_type):
    import hashlib
    from app.services.plan_validation import is_meaningful_general_plan
    contract=project_text_contract(node_type)
    text=getattr(project,contract['output_field'],None)
    valid=is_meaningful_general_plan(text) if node_type=='plan' else isinstance(text,str) and len(text.strip())>=200
    return {'status':'passed' if valid else 'failed','node_type':node_type,
        'field':contract['output_field'],'characters':len(text or ''),
        'sha256':hashlib.sha256((text or '').encode()).hexdigest(),
        'source':'original executor output; business commit/status require separate evidence',
        'reason':None if valid else 'native project text requirement not satisfied',
        'semantic_quality_verified':False,'human_approval_preserved':True}


async def observe_project_text_result(project,node_type):
    from app.services.observation import _attempt,record_fact
    ctx=_attempt.get()
    if not ctx or ctx.scope.mode!='managed' or node_type not in {'plan','script'}:return
    proof=verify_project_text(project,node_type)
    await record_fact('node_result_verification',proof,body={proof['field']:getattr(project,proof['field'],None)})
    if proof['status']!='passed':raise RuntimeError(proof['reason'])


def split_contract():
    return {'input':'original voiceover.txt snapshot','output':'project.db frames ordered by number',
        'minimum_frames':2,'formats':['replace_frames_json','dash_separated_blocks','native_local_voiceover_fallback'],
        'effects':['replace_all_frames','original XLSX export and snapshots','original split metadata'],
        'managed_verification':['scoped project','unique nonempty UUID','unique sequential number',
            'nonempty voiceover','complete ordered voiceover after whitespace normalization'],
        'semantic_quality_verified':False,'retry_policy':'original runtime only; no supervisor recovery/profile'}


def verify_split_frames(frames, *, project_id, voiceover):
    """Verify actual frame rows against the captured input, without changing them."""
    import hashlib
    from app.services.observation import digest
    rows=[{'project_id':fr.project_id,'uuid':fr.uuid,'number':fr.number,
           'voiceover_text':fr.voiceover_text or ''} for fr in frames]
    errors=[]
    if len(rows)<2:errors.append('minimum_frames')
    if any(r['project_id']!=project_id for r in rows):errors.append('project_scope')
    uuids=[r['uuid'] for r in rows]
    if any(not isinstance(u,str) or not u.strip() for u in uuids) or len(set(uuids))!=len(uuids):errors.append('frame_identity')
    if [r['number'] for r in rows]!=list(range(1,len(rows)+1)):errors.append('frame_order')
    if any(not isinstance(r['voiceover_text'],str) or not r['voiceover_text'].strip() for r in rows):errors.append('empty_voiceover')
    normalize=lambda text:' '.join(text.split())
    known=isinstance(voiceover,str) and bool(voiceover.strip())
    output=' '.join(r['voiceover_text'] for r in rows)
    preserved=normalize(output)==normalize(voiceover) if known else None
    if preserved is False:errors.append('voiceover_changed_or_missing')
    return {'status':'failed' if errors else ('passed' if known else 'unknown'),
        'node_type':'split','frame_count':len(rows),'frame_set_sha256':digest(rows),
        'input_sha256':hashlib.sha256(voiceover.encode()).hexdigest() if known else None,
        'voiceover_preserved':preserved,'errors':errors,
        'reason':'; '.join(errors) if errors else (None if known else 'captured input unavailable'),
        'business_commit_claim':False,'semantic_quality_verified':False}


async def record_split_input(voiceover):
    from app.services.observation import _attempt,record_fact
    ctx=_attempt.get()
    if ctx is None:return
    ctx.split_input_voiceover=voiceover
    await record_fact('node_input',{'contract':'native_split','source':'original voiceover file snapshot'},
        body={'voiceover':voiceover})


async def record_split_parse(reply,frames_spec):
    import json
    from types import SimpleNamespace
    from app.services.observation import _attempt,record_fact
    ctx=_attempt.get()
    if ctx is None:return
    try:json.loads(reply);syntax_valid=True
    except (ValueError,TypeError):syntax_valid=False
    frames=[SimpleNamespace(project_id=ctx.scope.project_id,uuid='extracted-'+str(i),number=i,
        voiceover_text=str(f.get('voiceover_text') or f.get('закадр') or f.get('voiceover') or '').strip())
        for i,f in enumerate(frames_spec,1)]
    proof=verify_split_frames(frames,project_id=ctx.scope.project_id,voiceover=ctx.split_input_voiceover)
    await record_fact('parse_result',{'contract':'native_split','syntax_valid':syntax_valid,
        'parsed':bool(frames_spec),'payload_present':bool(frames_spec),'payload_required':False,
        'schema_valid':proof['status']=='passed','frame_count':len(frames_spec),
        'native_fallback_allowed':True,'semantic_quality_verified':False},request_id=ctx.last_request_id,
        body={'reply':reply,'extracted_frames':frames_spec})
    await record_fact('runtime_check',{'check':'native_split_voiceover_coverage','performed':True,
        **proof,'source':'actual extraction; no DB commit claim'},request_id=ctx.last_request_id)


async def observe_split_result(session,project,node_type):
    from sqlalchemy import select
    from app.models import Frame
    from app.services.observation import _attempt,record_fact
    ctx=_attempt.get()
    if not ctx or ctx.scope.mode!='managed' or node_type!='split':return
    frames=(await session.scalars(select(Frame).where(Frame.project_id==project.id).order_by(Frame.number))).all()
    proof=verify_split_frames(frames,project_id=project.id,voiceover=getattr(ctx,'split_input_voiceover',None))
    await record_fact('node_result_verification',{**proof,'source':'original executor frame rows; commit requires outbox'},
        body={'frames':[{'uuid':f.uuid,'number':f.number,'voiceover_text':f.voiceover_text} for f in frames]})
    if proof['status']!='passed':
        from app.services.actions_v1 import ActionSupervisor
        await ActionSupervisor(ctx.observer.master).set_held(project_id=ctx.scope.project_id,
            execution_id=ctx.scope.execution_id,held=True,initiator='native_split_verifier',
            rationale=proof['reason'],idempotency_key='split-verify:'+ctx.attempt_id,operator_request=False)
        raise RuntimeError('native split verification not confirmed: '+str(proof['reason']))
