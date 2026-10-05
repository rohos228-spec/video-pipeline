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
    # Recovery-readiness data. Defaults are the conservative ones; a node opts in
    # only after its native executor/verifier path has been shown to be sound.
    # profile_compatible: an approved provider profile (effective_body overlay on the
    #   original single text Responses request) may be applied to this node's attempt.
    # native_json_retry: the native executor emits a `json_retry` fact before it fails
    #   (excel_gpt). Nodes without such an inner retry are proven exhausted by the
    #   runtime failure counter alone (step_failure total_fails/abandoned_at).
    # recovery_continuation: what the supervisor may do after a verified recovery.
    #   'excel_gpt_chain' = original excel_gpt chain continuation; 'none' = nothing,
    #   the original runtime (and any mandatory HITL) continues by itself.
    # volatile_attachment_names: the request text embeds wall-clock-stamped attachment file
    #   names, so the recovery's first-body identity check masks exactly those stamps.
    profile_compatible: bool = False
    volatile_attachment_names: bool = False
    native_json_retry: bool = True
    recovery_continuation: str = "excel_gpt_chain"

    def contract(self):
        return {**asdict(self), "semantic_quality_verified": False,
                "write_authority": "existing worker only",
                "arbitrary_shell_code_paths": False}


_JOB_PHASES = ("reserved", "submitted", "queued", "running", "completed",
               "downloading", "verifying", "verified", "failed", "cancelled", "unknown")
# Declarative capability data, one CapabilitySpec per node type (see
# app/orchestrator/node_spec.py). NodeCapability payloads (asdict/contract) are
# unchanged: acceptance/verifier/contract/successor live only in the spec.
# `acceptance` names the accepted-evidence scenario; managed is forced False
# without it. `verifier` / `contract` are NAMES in the registries populated
# below / in decision_v1.py.
_CAPABILITIES: dict[str, NodeCapability] = {}


def register_capability_spec(spec, *, replace: bool = False) -> None:
    """Add one node's capability data to the registry (and the derived
    NodeCapability view used by capability_for)."""
    from app.orchestrator import node_spec
    if spec.node_type in node_spec._CAPABILITY_SPECS and not replace:
        raise ValueError(f"capability already registered: {spec.node_type!r}")
    node_spec._CAPABILITY_SPECS[spec.node_type] = spec
    _CAPABILITIES[spec.node_type] = spec.to_capability()


def unregister_capability_spec(node_type: str) -> None:
    from app.orchestrator import node_spec
    node_spec._CAPABILITY_SPECS.pop(node_type, None)
    _CAPABILITIES.pop(node_type, None)


def _register_builtin_capabilities() -> None:
    from app.orchestrator.node_spec import CapabilitySpec as C
    text_phases = ('running', 'parse', 'validate', 'apply', 'commit', 'human_approval')
    for spec in (
        C('plan', 'project_text', 'make_plan.run → xlsx_step_runners.run_plan_xlsx',
            text_phases, ('project_database', 'local_xlsx', 'hitl_request'),
            READ_ACTIONS + ('retry_same', 'retry_profile', 'finish'), True,
            profile_compatible=True, volatile_attachment_names=True, native_json_retry=False,
            recovery_continuation='none', acceptance='native_project_text',
            verifier='project_text_result', contract='project_text'),
        C('script', 'project_text', 'make_script.run → xlsx_step_runners.run_script_xlsx',
            text_phases, ('project_database', 'local_xlsx', 'hitl_request'),
            READ_ACTIONS + ('retry_same', 'retry_profile', 'finish'), True,
            profile_compatible=True, volatile_attachment_names=True, native_json_retry=False,
            recovery_continuation='none', acceptance='native_project_text',
            verifier='project_text_result', contract='project_text'),
        C('split', 'frame_set', 'split_frames.run → xlsx_step_runners.run_split_xlsx',
            ('running', 'parse', 'apply', 'commit', 'verify'), ('project_database', 'local_xlsx'),
            READ_ACTIONS, True, acceptance='native_split',
            verifier='split_result', contract='frame_rows'),
        C('image_prompts', 'frame_prompts',
            'generate_image_prompts.run → xlsx_step_runners.run_img_pr_xlsx',
            ('running', 'parse', 'validate', 'apply', 'commit', 'verify'),
            ('project_database', 'local_xlsx', 'local_checkpoint'), READ_ACTIONS, True,
            acceptance='native_image_prompts', verifier='image_prompts_result',
            contract='frame_rows'),
        C('excel_gpt', 'structured', 'enrich_xlsx.run',
            ('running', 'parse', 'validate', 'apply', 'commit'), ('project_database',),
            READ_ACTIONS + ('retry_same', 'retry_profile', 'finish'), True, profile_compatible=True,
            acceptance='excel_gpt_group', contract='structured'),
        C('images', 'image', 'generate_images.run → kie_http',
            _JOB_PHASES, ('provider_job', 'local_file', 'artifact_row'), READ_ACTIONS, True,
            acceptance='synthetic_image_scenario', contract='media_jobs',
            successor='continue_managed_image'),
        C('videos', 'video', 'generate_videos.run → kie_http',
            _JOB_PHASES, ('provider_job', 'local_file', 'artifact_row'), READ_ACTIONS, False,
            contract='media_jobs'),
        C('audio', 'audio', 'generate_audio.run → elevenlabs_api',
            ('reserved', 'submitted', 'verifying', 'verified', 'alignment', 'failed', 'unknown'),
            ('provider_generation', 'local_file', 'frame_timing', 'artifact_row'), READ_ACTIONS, False,
            contract='media_jobs'),
        C('assemble', 'assembly', 'assemble.run → assembly.assemble',
            ('inputs', 'processing', 'subtitles', 'verifying', 'verified', 'failed', 'unknown'),
            ('local_file', 'artifact_row'), READ_ACTIONS, False, contract='media_jobs'),
        C('sfx_plan', 'structured', 'sfx_plan.plan_sfx_events → text_job',
            ('running', 'parse', 'validate', 'apply', 'commit'),
            ('project_database', 'local_file', 'local_checkpoint'), READ_ACTIONS, False,
            verifier='sfx_result', contract='structured'),
        C('sfx_gen', 'audio',
            'generate_sfx.run → sfx_gen.generate_sfx_files → elevenlabs_api/local_synth',
            ('running', 'generating', 'writing', 'verifying', 'verified', 'failed', 'unknown'),
            ('local_file', 'project_database'), READ_ACTIONS, False,
            verifier='sfx_result', contract='media_jobs'),
        C('publish', 'publication', 'publish.run',
            ('input_validation', 'dry_run', 'blocked'), ('external_accounts',),
            ('read_events', 'read_contract', 'human_review'), False, contract='media_jobs'),
    ):
        register_capability_spec(spec)


# Recovery decisions for media/paid-or-external node types. Pure declarative data kept OUTSIDE
# NodeCapability on purpose: contract() is built with asdict() and is persisted/sent to the
# decision layer, so a new dataclass field would change those payloads. Nothing reads this
# table at runtime; docs/MEDIA_NODES_READINESS.md and tests/test_media_nodes_recovery_decision.py
# are its only consumers. Every entry is human-only: no supervisor retry for any media node.
# Facts are code-grounded (file:line in the doc); "reason" is the honest short justification.
_MEDIA_HUMAN_ONLY = "human_only"
MEDIA_RECOVERY_DECISIONS = {
    "hero": {"decision": _MEDIA_HUMAN_ONLY, "cost_class": "paid_per_image",
        "provider": "outsee/kie via generate_image_with_retries",
        "reason": "No capability and no managed lifecycle; native ladder already spends up to 6 paid "
                  "submits per image with no durable job id, so a supervisor retry could double-pay."},
    "items": {"decision": _MEDIA_HUMAN_ONLY, "cost_class": "paid_per_image",
        "provider": "outsee/kie via generate_image_with_retries",
        "reason": "No capability and no managed lifecycle; same up-to-6-submit native ladder as hero, "
                  "no durable provider job id (a retry could double-pay); step rolls back to hero_ready for the operator."},
    "images": {"decision": _MEDIA_HUMAN_ONLY, "cost_class": "paid_per_image",
        "provider": "kie (managed, synthetic loopback only) / outsee (native)",
        "reason": "Managed route is synthetic-loopback only (no authorized production network profile); "
                  "a failed or unknown job is terminal and never resubmitted; uncertain submit may "
                  "already be charged."},
    "videos": {"decision": _MEDIA_HUMAN_ONLY, "cost_class": "paid_per_clip_highest",
        "provider": "outsee/kie (Seedance, Kling, Veo ladder)",
        "reason": "managed=False; native ladder is up to 4 primary + 3 fallback paid submits per clip "
                  "with no durable job id; highest per-attempt cost, a blind retry can double-pay."},
    "audio": {"decision": _MEDIA_HUMAN_ONLY, "cost_class": "paid_per_character",
        "provider": "elevenlabs_api (direct HTTP)",
        "reason": "managed=False; request has no idempotency key and the native client already retries "
                  "3 times on timeout/429/5xx, so a timeout after acceptance may be charged twice."},
    "music": {"decision": _MEDIA_HUMAN_ONLY, "cost_class": "paid_per_generation",
        "provider": "kie suno (direct) or browser outsee",
        "reason": "No capability; Suno family has no approved managed route and the executor writes "
                  "an Artifact only after the paid generation, so a retry re-submits and re-pays."},
    "sfx_gen": {"decision": _MEDIA_HUMAN_ONLY, "cost_class": "paid_per_event_small_or_free_local",
        "provider": "elevenlabs sound-effects (or local_synth when no key)",
        "reason": "managed=False; per-event paid call without idempotency key, on error it silently "
                  "falls back to local synthesis, so provider outcome of the failed call is unknown."},
    "assemble": {"decision": _MEDIA_HUMAN_ONLY, "cost_class": "no_provider_spend_local_cpu",
        "provider": "local ffmpeg (+ local ASR timing)",
        "reason": "No provider spend, but the output is a final deliverable behind approve_final; "
                  "no recovery verifier exists, so it stays human-only until one is accepted."},
    "publish": {"decision": _MEDIA_HUMAN_ONLY, "cost_class": "external_side_effect_not_money",
        "provider": "social publishers (external accounts)",
        "reason": "Posting to external accounts is irreversible; managed publication is hard-disabled "
                  "and a retry could publish twice."},
}


def media_recovery_decision(node_type: str):
    """Declarative human-only decision for a media node, or None for any other type."""
    return MEDIA_RECOVERY_DECISIONS.get(node_type)


def capability_for(node_type: str) -> NodeCapability:
    from app.orchestrator.node_registry import is_work_node_type
    from app.orchestrator.node_spec import capability_spec_for
    spec = capability_spec_for(node_type)
    if spec is not None and is_work_node_type(node_type):
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
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.models import Frame
    from app.services.observation import _attempt,record_fact
    ctx=_attempt.get()
    if not ctx or ctx.scope.mode!='managed' or node_type!='image_prompts':return
    project_id=ctx.scope.project_id
    read_error=None
    try:
        # Bind to the existing business DB without initialization, migration,
        # autoflush, commit, or repair of a failed native session.
        async with async_sessionmaker(session.bind,expire_on_commit=False,autoflush=False)() as reader:
            frames=(await reader.scalars(select(Frame).where(Frame.project_id==project_id)
                .order_by(Frame.number))).all()
        proof=verify_image_prompts(frames,project_id=project_id,snapshot=getattr(ctx,'image_prompt_input',None))
    except Exception as exc:
        read_error=exc;frames=None
        proof={'node_type':'image_prompts','status':'unknown','reason':'committed output unavailable',
            'error_type':type(exc).__name__,'frame_count':None,'frame_set_sha256':None,
            'semantic_quality_verified':False,'business_commit_claim':False}
    output={'project_id':project_id,'frames':None if frames is None else [
        {'uuid':f.uuid,'number':f.number,'voiceover_text':f.voiceover_text,
            'image_prompt':f.image_prompt,'attrs':f.attrs} for f in frames]}
    await record_fact('node_output',{'frame_count':None if frames is None else len(frames),
        'frame_set_sha256':proof['frame_set_sha256'],'source':'committed original executor rows; independent read session'},body=output)
    await record_fact('node_result_verification',proof,body=proof)
    ctx.image_prompt_verification_recorded=True
    if read_error:ctx.observer.mark_gap(ctx.scope.execution_id,read_error)
    if proof['status']!='passed':
        from app.services.actions_v1 import ActionSupervisor
        try:
            await ActionSupervisor(ctx.observer.master).set_held(project_id=project_id,execution_id=ctx.scope.execution_id,
                held=True,initiator='native_image_prompt_verifier',rationale=proof['reason'],
                idempotency_key='image-prompts-verify:'+ctx.attempt_id,operator_request=False)
        except Exception as exc:
            ctx.observer.mark_gap(ctx.scope.execution_id,exc)
            raise
        ctx.image_prompt_hold_saved=True
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
        'retry_policy':('original native runtime first; after it is exhausted with invalid_json/missing_payload '
            'ONE bounded supervisor retry_same or approved-profile retry of the same executor. '
            'Never approves the mandatory HITL; success is verified at the HITL boundary.')}


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
        # plan always needs the JSON field. script accepts JSON, a VOICEOVER block or plain
        # text, so a payload is "required" in the diagnostic sense only when NOTHING usable
        # was extracted (e.g. malformed JSON and no voiceover): that is the typed
        # invalid_json/missing_payload failure; a valid non-JSON voiceover never is.
        'payload_required': node_type == 'plan' or (node_type == 'script' and not text),
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


async def _observe_project_text(session, project, node_type):
    # Adapter to the common verifier signature; resolves the module global at
    # call time so the original function stays the single implementation.
    await observe_project_text_result(project, node_type)


def _register_builtin_verifiers() -> None:
    """Name the existing result-observation hooks (observation.observe_attempt
    looks them up by node type instead of an if-chain)."""
    from app.orchestrator.node_spec import VerifierSpec, bind_verifier, register_verifier
    me = 'app.services.node_capabilities:'
    register_verifier(VerifierSpec('project_text_result', me + '_observe_project_text',
        managed_only=True, success_errors='raise'))
    register_verifier(VerifierSpec('split_result', me + 'observe_split_result',
        managed_only=True, success_errors='raise'))
    register_verifier(VerifierSpec('image_prompts_result', me + 'observe_image_prompt_result',
        managed_only=True, success_errors='raise', on_error=True,
        error_done_flag='image_prompt_verification_recorded',
        error_gap_exempt_flag='image_prompt_hold_saved'))
    register_verifier(VerifierSpec('sfx_result', 'app.services.sfx_plan:observe_sfx_result',
        success_errors='gap', success_flag='sfx_result_observed', on_error=True,
        error_done_flag='sfx_result_observed'))
    register_verifier(VerifierSpec('animation_result',
        'app.services.native_animation_contracts:observe_animation_result',
        success_errors='gap', on_error=True, error_done_flag='animation_verification_recorded'))
    # animation_prompts has no capability (unsupported, observed only).
    bind_verifier('animation_prompts', 'animation_result')


_register_builtin_capabilities()
_register_builtin_verifiers()
