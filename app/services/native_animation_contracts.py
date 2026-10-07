"""Bounded read-only evidence for the original animation prompt executor.

Capture input before ``make_animation_prompts.run`` and compare it with Frame
rows independently read after the executor. These helpers neither run/apply
generation nor change statuses. The original harness and committed observation
outbox remain separate authorities for their own checks and application facts.
"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace


_INPUT_FIELDS = (
    "id", "project_id", "uuid", "number", "sort_key", "scene_id",
    "voiceover_text", "image_prompt", "attrs",
)
_SNAPSHOT_SOURCE = "original animation executor Frame inputs"
_UNCLAIMED = {
    "business_commit_claim": False,
    "semantic_quality_verified": False,
    "video_quality_verified": False,
    "managed_eligible": False,
}


# C05: what a recorded degradation of animation_prompts means for the step. The executor already carries on
# by a local fallback when the provider is down; this fixes the POLICY for that outcome in one place:
# the step counts as "success with degradation" (never silent success, never an automatic failure), the
# incident `animation_degraded` is its visible record, and a repeated paid model request is a human decision.
ANIMATION_DEGRADATION_POLICY = {
    "outcome": "success_with_degradation",
    "visible_as": "incident animation_degraded (warning, human_review)",
    "step_status_unchanged": True,
    "paid_retry": "human decision only; no automatic retry",
    "operator_text_ru": ("Промты анимации получены запасным путём (сбой провайдера). Шаг принят как "
                         "«успех с деградацией»: качество не проверялось. Повторный платный запрос "
                         "выполняется только по вашему решению."),
}


def animation_prompts_contract():
    """Describe the shipped executor, parser, writes and coverage rules."""
    from app.services import animation_prompt_gpt as native
    from app.services.node_capabilities import capability_for
    from app.services.node_write_contract import ANIM_PROMPT_FIELDS

    capability = capability_for("animation_prompts")
    return {
        "node_type": "animation_prompts",
        "executor": "app.orchestrator.steps.make_animation_prompts.run",
        "parser": "app.services.animation_prompt_gpt.parse_animation_reply",
        "writer": "app.services.db_apply.apply_ops",
        "harness": "app.services.agent_harness.harness_gate_or_raise",
        "write_contract": "node_write_contract.filter_ops_for_node(node_kind=anim_pr)",
        "allowed_fields": sorted(ANIM_PROMPT_FIELDS),
        "input": "original scoped Frame rows; native prompt; optional scene images",
        "output": "same Frame identities; animation_prompt and native second-shot attrs",
        "min_characters": native.MIN_ANIM_PROMPT_LEN,
        "shot2_min_characters": native.MIN_SHOT2_VIDEO_PROMPT_LEN,
        "primary_png_required": False,
        "child_exemption": False,
        "target_predicate": "animation_prompt_gpt.frame_has_usable_image_prompt",
        "primary_ready_predicate": "animation_prompt_gpt.has_animation_prompt_for_frame",
        "placeholder_predicate": "generation_options.is_skippable_empty_prompt",
        "shot2_target_predicate": "animation_prompt_gpt.frame_needs_shot2_video_prompt",
        "shot2_ready_predicate": "animation_prompt_gpt.has_animation_prompt_shot2_for_frame",
        "shot2_source": "declared native shot02 plus disk image; existing XLSX R64 precedes attrs",
        "sidecar_shot2": "not queued when finalize_status=False",
        "native_partial_extraction_preserved": True,
        "raw_apply_ops_blob_is_prompt": False,
        "write_authority": "existing worker only",
        "arbitrary_shell_code_paths": False,
        "managed": capability.managed,
        "actions": capability.actions,
        "payload_required": True,
        "retry_policy": "ONE supervisor retry_same per node and execution, only for invalid_json / "
                        "missing_payload of a batch reply while NO batch has been applied "
                        "(any apply_input/data_applied/apply_error of the failed attempt forbids it); "
                        "no profile retry (vision input cannot be rebuilt by a profile)",
        "degradation_facts": {
            "fact_kind": "animation_degraded",
            "reasons": ["local_fallback (provider outage: rest of shot_01 filled by the local composer, no model, no images)",
                        "shot2_skipped (provider outage: remaining shot_02 prompts not made, step continues)"],
            "incident": "animation_degraded (non-blocking warning, human_review, never a recovery incident)",
            "retry": "none: a degradation is not a failure of the attempt",
            "policy": ANIMATION_DEGRADATION_POLICY,
            "failed_result_check": "incident verification_hold (blocking when status=failed, warning when unknown)"},
        **_UNCLAIMED,
    }


def _input_row(frame):
    return {field: deepcopy(getattr(frame, field)) for field in _INPUT_FIELDS}


def animation_prompt_input_snapshot(frames, *, project_id, project, finalize_status=True):
    """Detach native input rows and capture the original pending-shot02 rule.

    ``required_prompt`` covers every usable primary input, including children
    with their own image prompt. ``required_shot2`` captures pending native work
    only when the full executor finalizes; sidecar execution does not queue it.
    """
    from app.services import animation_prompt_gpt as native

    if type(finalize_status) is not bool:
        raise TypeError("finalize_status must be a boolean")
    rows = []
    for frame in sorted(frames, key=lambda item: item.number):
        row = _input_row(frame)
        row["required_prompt"] = native.frame_has_usable_image_prompt(frame)
        row["required_shot2"] = bool(
            finalize_status and native.frame_needs_shot2_video_prompt(project, frame)
        )
        rows.append(row)
    return {
        "schema_version": 1,
        "source": _SNAPSHOT_SOURCE,
        "project_id": project_id,
        "finalize_status": finalize_status,
        "frames": rows,
    }


def _complete_snapshot(snapshot):
    if not isinstance(snapshot, dict):
        return False
    if (snapshot.get("schema_version") != 1 or snapshot.get("source") != _SNAPSHOT_SOURCE
            or "project_id" not in snapshot or type(snapshot.get("finalize_status")) is not bool):
        return False
    rows = snapshot.get("frames")
    required = set(_INPUT_FIELDS) | {"required_prompt", "required_shot2"}
    return bool(isinstance(rows, list) and rows and all(
        isinstance(row, dict) and required <= row.keys()
        and type(row["required_prompt"]) is bool and type(row["required_shot2"]) is bool
        and (row["attrs"] is None or isinstance(row["attrs"], dict))
        and (row["image_prompt"] is None or isinstance(row["image_prompt"], str))
        for row in rows
    ))


def _identity_errors(rows, *, source, project_id):
    errors = []
    if any(row["project_id"] != project_id for row in rows):
        errors.append(f"{source}_project_outside_scope")
    uuids = [row["uuid"] for row in rows]
    valid_uuids = [uid for uid in uuids if isinstance(uid, str) and uid.strip()]
    if len(valid_uuids) != len(uuids):
        errors.append(f"{source}_missing_or_invalid_uuid")
    if len(set(valid_uuids)) != len(valid_uuids):
        errors.append(f"{source}_duplicate_uuid")
    for field in ("id", "number"):
        values = [row[field] for row in rows]
        valid_values = [value for value in values if type(value) is int]
        if len(valid_values) != len(values):
            errors.append(f"{source}_invalid_{field}")
        if len(set(valid_values)) != len(valid_values):
            errors.append(f"{source}_duplicate_{field}")
    return errors


def _preserved_attrs(attrs, animation_key):
    # The original writer materializes None attrs as {}; only its native
    # animation_prompt_shot2 attr is permitted to change for this node.
    return {key: value for key, value in (attrs or {}).items() if key != animation_key}


def verify_animation_prompts(frames, *, project_id, project, snapshot):
    """Verify actual scoped results without applying data or inferring commit.

    Unknown input/output evidence never becomes a successful coverage check.
    Iteration order is immaterial; identities, native number and V2 sort_key
    remain exact. Native primary coverage and snapshot-pending shot02 coverage
    are checked independently, without replacing the original harness.
    """
    from app.generation_options import is_skippable_empty_prompt
    from app.services import animation_prompt_gpt as native
    from app.services.observation import digest

    proof = {
        "node_type": "animation_prompts",
        "source": "actual original executor Frame rows; committed outbox proves application separately",
        "frame_count": None,
        "frame_set_sha256": None,
        "input_sha256": digest(snapshot) if snapshot is not None else None,
        "voiceover_preserved": False,
        "image_prompts_preserved": False,
        "target_frame_count": 0,
        "shot2_target_frame_count": 0,
        **_UNCLAIMED,
    }
    if not _complete_snapshot(snapshot):
        return {
            **proof, "status": "unknown",
            "reason": "captured input unavailable or incomplete", "errors": [],
        }

    try:
        rows = []
        for frame in frames:
            row = _input_row(frame)
            row["animation_prompt"] = deepcopy(frame.animation_prompt)
            rows.append(row)
        rows.sort(key=lambda row: row["number"])
    except Exception as exc:
        return {
            **proof, "status": "unknown", "reason": "actual result rows unavailable",
            "error_type": type(exc).__name__, "errors": [],
        }
    proof.update(frame_count=len(rows), frame_set_sha256=digest(rows))
    before = snapshot["frames"]
    errors = _identity_errors(before, source="input", project_id=project_id)
    errors.extend(_identity_errors(rows, source="output", project_id=project_id))
    if snapshot["project_id"] != project_id:
        errors.append("input_project_outside_scope")
    if getattr(project, "id", None) != project_id:
        errors.append("native_project_outside_scope")

    before_ids = [row["uuid"] for row in before if isinstance(row["uuid"], str)]
    after_ids = [row["uuid"] for row in rows if isinstance(row["uuid"], str)]
    if len(before) != len(rows) or set(before_ids) != set(after_ids):
        errors.append("frame_set_changed")
    by_uuid = {row["uuid"]: row for row in rows if isinstance(row["uuid"], str)}
    voiceover_preserved = True
    image_prompts_preserved = True
    uncertain = []
    targets = 0
    second_targets = 0
    for previous in before:
        uid = previous["uuid"]
        current = by_uuid.get(uid) if isinstance(uid, str) else None
        required_primary = native.frame_has_usable_image_prompt(
            SimpleNamespace(image_prompt=previous["image_prompt"])
        )
        targets += int(required_primary)
        second_targets += int(previous["required_shot2"])
        if previous["required_prompt"] is not required_primary:
            errors.append(f"input_target_rule_changed:{uid}")
        if not snapshot["finalize_status"] and previous["required_shot2"]:
            errors.append(f"input_sidecar_shot2_rule_changed:{uid}")
        if current is None:
            voiceover_preserved = False
            image_prompts_preserved = False
            continue

        for field in _INPUT_FIELDS:
            if field == "attrs":
                continue
            if current[field] != previous[field]:
                errors.append(f"{field}_changed:{uid}")
                if field == "voiceover_text":
                    voiceover_preserved = False
                elif field == "image_prompt":
                    image_prompts_preserved = False
        if current["attrs"] is not None and not isinstance(current["attrs"], dict):
            errors.append(f"invalid_output_attrs:{uid}")
        elif _preserved_attrs(current["attrs"], native.SHOT2_VIDEO_PROMPT_ATTR) != _preserved_attrs(
            previous["attrs"], native.SHOT2_VIDEO_PROMPT_ATTR
        ):
            errors.append(f"non_animation_attrs_changed:{uid}")

        actual = SimpleNamespace(**current)
        primary = current["animation_prompt"]
        if required_primary and (
            not isinstance(primary, str)
            or not native.has_animation_prompt_for_frame(project, actual)
            or is_skippable_empty_prompt(primary)
        ):
            errors.append(f"missing_or_unusable_primary_animation:{uid}")

        if snapshot["finalize_status"]:
            try:
                # Captured obligations cannot disappear because a declaration
                # or PNG was removed. Preserve the original ready predicate,
                # including its R64-over-attrs precedence when XLSX exists.
                if previous["required_shot2"]:
                    if not native.has_animation_prompt_shot2_for_frame(project, actual):
                        errors.append(f"missing_native_shot2_animation:{uid}")
                elif native.frame_needs_shot2_video_prompt(project, actual):
                    errors.append(f"pending_native_shot2_animation:{uid}")
            except Exception as exc:
                uncertain.append(f"shot2_evidence_unavailable:{uid}:{type(exc).__name__}")

    proof.update(
        voiceover_preserved=voiceover_preserved,
        image_prompts_preserved=image_prompts_preserved,
        target_frame_count=targets,
        shot2_target_frame_count=second_targets,
    )
    return {
        **proof,
        "status": "failed" if errors else "unknown" if uncertain else "passed",
        "reason": errors[0] if errors else uncertain[0] if uncertain else None,
        "errors": errors,
        "unknown_checks": uncertain,
    }


async def _animation_context():
    from app.models import WorkflowRun
    from app.services.excel_gpt_node import effective_node_type
    from app.services.observation import _attempt

    ctx = _attempt.get()
    if (ctx is None or ctx.scope.observer_state == "stopped"
            or getattr(ctx, "native_attempt_closed", False)):
        return None
    node_type = getattr(ctx, "native_node_type", None)
    if node_type is None:
        # Older/internal contexts still require the immutable node identity.
        # A copied sidecar context cannot turn an image NodeRun into animation.
        async with ctx.observer.master() as master:
            run = await master.get(WorkflowRun, ctx.scope.workflow_run_id)
            node = next((node for node in (run.nodes_snapshot if run else [])
                         if node.get("id") == ctx.node_key), None)
        node_type = effective_node_type(node) if node is not None else None
        ctx.native_node_type = node_type
    return ctx if node_type == "animation_prompts" else None


async def record_animation_input(project, frames, finalize_status=True):
    """Capture detached native inputs without flushing or changing authority."""
    from app.services.observation import digest, record_fact

    ctx = await _animation_context()
    if ctx is None:
        return
    snapshot = animation_prompt_input_snapshot(
        frames, project_id=ctx.scope.project_id, project=project,
        finalize_status=finalize_status,
    )
    ctx.animation_prompt_input = snapshot
    await record_fact("node_input", {
        "contract": "native_animation_prompts", "frame_count": len(snapshot["frames"]),
        "input_sha256": digest(snapshot),
    }, body=snapshot)


async def record_animation_parse(reply, pairs, ops, *, shot, target_uuids):
    """Describe actual native extraction; never apply or repair its output."""
    import json
    from dataclasses import asdict
    from app.services.observation import (
        completed_model_request_id, digest, record_fact,
    )

    ctx = await _animation_context()
    if ctx is None:
        return
    try:
        json.loads(reply)
        syntax_valid = True
    except (ValueError, TypeError):
        syntax_valid = False
    ids = [op.get("frame_uuid") for op in ops]
    scoped = bool(ids) and all(isinstance(uid, str) and uid for uid in ids)
    scoped = scoped and len(set(ids)) == len(ids) and set(ids) <= set(target_uuids)
    request_id = completed_model_request_id()
    await record_fact("parse_result", {
        "contract": "native_animation_prompts", "shot": shot,
        "syntax_valid": syntax_valid, "parsed": bool(pairs and ops),
        # Every batch needs usable pairs; the native executor raises right after a batch
        # without them. A reply that is not a JSON object is normal here (the reply format
        # is "image id / animation text" pairs), so syntax_valid=False alone says nothing.
        "payload_required": True, "payload_present": bool(pairs),
        "nonempty_payload_required": True,
        "payload_format": "animation_pairs_or_apply_ops",
        "schema_valid": bool(scoped), "ops_count": len(ops),
        "batch_coverage_complete": bool(scoped and set(ids) == set(target_uuids)),
        "parser": "original animation_prompt_gpt.parse_animation_reply and filtered native ops",
        "native_partial_extraction_preserved": True,
        "semantic_quality_verified": False, "sha256": digest(ops),
    }, body={"text": reply, "pairs": [asdict(pair) for pair in pairs],
             "extracted_ops": ops}, request_id=request_id)
    await record_fact("runtime_check", {
        "check": "native_animation_prompt_batch", "shot": shot,
        "status": "passed" if scoped else "failed", "performed": True,
        "business_commit_claim": False,
    }, request_id=request_id)


async def observe_animation_result(session, project, node_type="animation_prompts"):
    """Read committed output independently, including after native SQL failure.

    The reader shares the existing bind and performs no initialization, autoflush,
    rollback or repair of the executor session. Missing provenance stays unknown.
    This observation does not grant managed admission or affect runtime status.
    """
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from app.models import Frame, Project
    from app.services.observation import record_fact

    ctx = await _animation_context()
    if (ctx is None or node_type != "animation_prompts"
            or getattr(ctx, "animation_verification_recorded", False)):
        return
    project_id = ctx.scope.project_id  # Native ORM attributes may be expired.
    frames = None
    read_error = None
    try:
        async with async_sessionmaker(
            session.bind, expire_on_commit=False, autoflush=False,
        )() as reader:
            native_project = await reader.get(Project, project_id)
            if native_project is None:
                raise LookupError("native animation project unavailable")
            frames = list((await reader.scalars(select(Frame).where(
                Frame.project_id == project_id).order_by(Frame.number))).all())
            proof = verify_animation_prompts(
                frames, project_id=project_id, project=native_project,
                snapshot=getattr(ctx, "animation_prompt_input", None),
            )
    except Exception as exc:
        read_error = exc
        frames = None
        proof = {"node_type": node_type, "status": "unknown",
                 "reason": "committed native result unavailable",
                 "error_type": type(exc).__name__, "frame_set_sha256": None,
                 "input_sha256": None, **_UNCLAIMED}
    output = {"project_id": project_id, "frames": None if frames is None else [
        {**_input_row(frame), "animation_prompt": deepcopy(frame.animation_prompt)}
        for frame in frames
    ]}
    await record_fact("node_output", {
        "contract": "native_animation_prompts",
        "frame_count": None if frames is None else len(frames),
        "frame_set_sha256": proof["frame_set_sha256"],
        "source": "committed original executor rows; independent read session",
    }, body=output)
    await record_fact("node_result_verification", proof, body=proof)
    ctx.animation_verification_recorded = True
    if read_error is not None:
        ctx.observer.mark_gap(ctx.scope.execution_id, read_error)
