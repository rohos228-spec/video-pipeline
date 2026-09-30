"""Read-only contracts for native camera staging and scene assembly rows.

Callers supply already loaded native rows and captured input/payload evidence.
No session, file, checkpoint, provider, worker, FSM, or approval is changed here.
``passed`` describes only the stated structural checks. Transaction provenance,
managed execution, artistic quality, Entity writes and scene-space effects are
not proved by this helper. Missing evidence remains explicitly unknown.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from types import SimpleNamespace
from typing import Any


_SCHEMA = "scene_native_input_v1"
_ACTIONS = ("read_events", "read_contract", "wait", "human_review")
_FRAME_FIELDS = (
    "id", "project_id", "uuid", "number", "sort_key", "scene_id",
    "voiceover_text", "meaning", "transition_from", "transition_to",
    "duration_seconds", "start_ts", "end_ts", "image_prompt",
    "animation_prompt", "status", "attrs",
)
_IDENTITY_FIELDS = (
    "id", "project_id", "uuid", "number", "sort_key", "scene_id", "voiceover_text",
)
_CELL_FIELDS = (
    "project_id", "agent", "kind", "target_key", "field", "value",
    "seq", "vo_offset", "status", "error",
)
_REFERENCES = (
    "app/services/excel_gpt_node.py:effective_node_type/sd_agent_marker",
    "app/orchestrator/node_registry.py:spec_for_node/SD_AGENT_STEP_CODES",
    "app/orchestrator/pipeline.py:advance_project",
    "app/orchestrator/steps/scene_design.py:run/run_assemble",
    "app/models.py:SceneDesignCell/Frame/Scene",
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str | None:
    try:
        return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()
    except (TypeError, ValueError):
        return None


def _meta(project: Any) -> dict:
    value = getattr(project, "meta", None)
    return value if isinstance(value, dict) else {}


def _frame_state(frame: Any) -> dict:
    row = {key: copy.deepcopy(getattr(frame, key, None)) for key in _FRAME_FIELDS}
    row["status"] = getattr(row["status"], "value", row["status"])
    return row


def _cell_state(cell: Any) -> dict:
    return {key: copy.deepcopy(getattr(cell, key, None)) for key in _CELL_FIELDS}


def scene_contract(node: dict) -> dict:
    """Describe only actual registered camera/assembler routes; grant no actions."""
    from app.orchestrator.node_registry import SD_AGENT_STEP_CODES, spec_for_node
    from app.services.excel_gpt_node import effective_node_type, sd_agent_marker
    from app.services.scene_design.agents import CATEGORY_AGENTS

    node = node if isinstance(node, dict) else {}
    node_type = effective_node_type(node)
    marker = sd_agent_marker(node)
    common = {
        "node_type": node_type, "agent": marker, "supported": False,
        "status": "unknown", "step_code": None, "executor": "",
        "result_kind": "unsupported", "output_fields": [],
        "source_references": list(_REFERENCES), "actions": list(_ACTIONS),
        "human_approval_preserved": True, "human_approval_verified": False,
        "semantic_quality_verified": False, "managed_execution_verified": False,
        "business_commit_claim": False, "write_authority": "existing worker only",
    }
    camera = (node_type == "sd_agent" and marker == "camera"
              and marker in CATEGORY_AGENTS and marker in SD_AGENT_STEP_CODES.values())
    assembler = node_type == "sd_assemble" and marker == "assemble"
    if not (camera or assembler):
        return {**common, "reason": "scene route outside the bounded contract"}
    spec = spec_for_node(node)
    step_code = "scene_d" if camera else "scene_asm"
    if spec is None or spec.step_code != step_code:
        return {**common, "reason": "native registry route unavailable"}
    if camera:
        return {
            **common, "supported": True, "status": "known", "step_code": step_code,
            "executor": "scene_design.run -> runner.run_category_agents -> cells.store_cells",
            "result_kind": "scene_design_cells",
            "output_fields": ["SceneDesignCell." + key for key in _CELL_FIELDS],
            "gates": ["cells.slice_to_cells", "preserved frame rows and non-camera cells"],
            "snapshot_boundary": "after native DB-v2 backfill, before camera staging",
            "source_references": [*_REFERENCES,
                "app/services/scene_design/agents.py:CATEGORY_AGENTS",
                "app/services/scene_design/cells.py:slice_to_cells/store_cells"],
        }
    return {
        **common, "supported": True, "status": "known", "step_code": step_code,
        "executor": "scene_design.run_assemble -> apply.apply_scene_design -> db_apply.apply_ops",
        "result_kind": "scene_assembly",
        "output_fields": ["Project.meta.scene_registry", "Frame.attrs.shot01_id_scene",
                          "Frame.attrs.shot01_action", "Frame.attrs.place"],
        "gates": ["assembler.validate_payload", "native scene card normalization",
                  "payload fields match actual Frame.attrs"],
        "snapshot_boundary": "after native camera_expand, before assembly validation/apply",
        "unchecked_effects": ["Entity", "scene_space", "files", "transaction provenance"],
        "registry_ids_are_scene_primary_keys": False,
        "source_references": [*_REFERENCES,
            "app/services/scene_design/assembler.py:validate_payload",
            "app/services/scene_design/apply.py:apply_scene_design",
            "app/services/db_apply.py:FIELD_ALIASES/_normalize_scene_card/expand_scene_registry_onto_frames"],
    }


def scene_input_snapshot(project: Any, frames: Any, *, cells: Any = ()) -> dict:
    """Copy loaded scalar fields only. Caller chooses the native capture boundary."""
    from app.services.scene_design.runner import scene_design_enabled

    return {
        "schema": _SCHEMA, "project_id": getattr(project, "id", None),
        "enabled": scene_design_enabled(project),
        "script_text": getattr(project, "script_text", None),
        "general_plan": getattr(project, "general_plan", None),
        "scene_registry": copy.deepcopy(_meta(project).get("scene_registry")),
        "frames": [_frame_state(frame) for frame in frames],
        "cells": [_cell_state(cell) for cell in cells],
    }


def _snapshot_available(snapshot: Any) -> bool:
    return (
        isinstance(snapshot, dict) and snapshot.get("schema") == _SCHEMA
        and snapshot.get("project_id") is not None
        and {"enabled", "script_text", "general_plan", "scene_registry", "cells"} <= snapshot.keys()
        and isinstance(snapshot.get("frames"), list) and bool(snapshot["frames"])
        and all(isinstance(row, dict) and set(_FRAME_FIELDS) <= row.keys()
                for row in snapshot["frames"])
        and isinstance(snapshot["cells"], list)
        and all(isinstance(row, dict) and set(_CELL_FIELDS) <= row.keys()
                for row in snapshot["cells"])
        and _hash(snapshot) is not None
    )


def _proof(node_type: str, rows: list, snapshot: Any, *, cells: Any = (), registry: Any = None) -> dict:
    return {
        "node_type": node_type, "frame_count": len(rows),
        "sha256": _hash({"frames": rows, "cells": cells, "scene_registry": registry}),
        "input_sha256": _hash(snapshot) if isinstance(snapshot, dict) else None,
        "semantic_quality_verified": False, "business_commit_claim": False,
        "managed_execution_verified": False, "human_approval_preserved": True,
        "human_approval_verified": False,
        "verification_scope": "provided native row/payload structural integrity only",
    }


def _result(proof: dict, errors: Any = (), *, unknown: str | None = None) -> dict:
    errors = list(dict.fromkeys(errors))
    return {
        **proof, "status": "failed" if errors else "unknown" if unknown else "passed",
        "reason": errors[0] if errors else unknown, "errors": errors,
    }


def _frame_errors(project_id: Any, rows: list, snapshot: dict, *, camera: bool) -> list[str]:
    errors = []
    before = snapshot["frames"]
    if snapshot["project_id"] != project_id or any(r["project_id"] != project_id for r in before):
        errors.append("input_project_outside_scope")
    if any(r["project_id"] != project_id for r in rows):
        errors.append("output_project_outside_scope")
    before_ids, after_ids = ([r["uuid"] for r in group] for group in (before, rows))
    for ids in (before_ids, after_ids):
        if any(not isinstance(uid, str) or not uid.strip() for uid in ids):
            errors.append("missing_or_invalid_frame_uuid")
            return errors
        if len(set(ids)) != len(ids):
            errors.append("duplicate_frame_uuid")
    if before_ids != after_ids:
        errors.append("frame_set_or_order_changed")
    if len({r["number"] for r in rows}) != len(rows):
        errors.append("duplicate_frame_number")
    current = {r["uuid"]: r for r in rows}
    for previous in before:
        row = current.get(previous["uuid"])
        if row is None:
            continue
        for field in _FRAME_FIELDS if camera else _IDENTITY_FIELDS:
            if row[field] != previous[field]:
                errors.append("frame_" + field + "_changed:" + previous["uuid"])
    return errors


def verify_scene_agent(project: Any, node: dict, cells: Any, *, frames: Any,
                       snapshot: Any, agent_slice: Any) -> dict:
    """Match camera facts to the native converter, without writing its cells."""
    from app.services.scene_design import cells as native_cells, context_builder
    from app.services.scene_design.runner import scene_design_enabled

    frames = list(frames) if frames is not None else None
    cells = list(cells) if cells is not None else None
    rows = [_frame_state(frame) for frame in frames or ()]
    facts = [_cell_state(cell) for cell in cells or ()]
    registry = _meta(project).get("scene_registry")
    proof = {**_proof("sd_agent", rows, snapshot, cells=facts, registry=registry),
             "agent": "camera", "cell_count": sum(r["agent"] == "camera" for r in facts)}
    contract = scene_contract(node)
    if not contract["supported"] or contract["agent"] != "camera":
        return _result(proof, unknown="camera route unavailable or outside bounded contract")
    if not _snapshot_available(snapshot) or agent_slice is None or frames is None or cells is None:
        return _result(proof, unknown="captured camera input, slice or output unavailable")
    if snapshot["enabled"] is not True or not scene_design_enabled(project):
        return _result(proof, unknown="scene design disabled; pass-through is not output proof")
    if proof["sha256"] is None:
        return _result(proof, unknown="camera output cannot be structurally represented")
    errors = _frame_errors(project.id, rows, snapshot, camera=True)
    for field in ("script_text", "general_plan"):
        if getattr(project, field, None) != snapshot[field]:
            errors.append("project_" + field + "_changed")
    if registry != snapshot["scene_registry"]:
        errors.append("production_scene_registry_changed")
    if any(row["project_id"] != project.id for row in facts):
        errors.append("cell_project_outside_scope")
    target = [row for row in facts if row["agent"] == "camera"]
    if not target:
        errors.append("missing_camera_cells")
    if any(row["status"] != "ok" for row in target):
        errors.append("rejected_or_unknown_camera_cell")
    identities = [(r["kind"], r["target_key"], r["field"]) for r in target]
    if len(set(identities)) != len(identities):
        errors.append("duplicate_camera_cell")
    other_before = Counter(_json(r) for r in snapshot["cells"] if r["agent"] != "camera")
    other_after = Counter(_json(r) for r in facts if r["agent"] != "camera")
    if other_before != other_after:
        errors.append("non_camera_cells_changed")
    if (not isinstance(agent_slice, dict) or not isinstance(agent_slice.get("shot_plan"), list)
            or not agent_slice["shot_plan"] or any(not isinstance(r, dict) for r in agent_slice["shot_plan"])):
        return _result(proof, [*errors, "invalid_camera_slice"])
    expected = native_cells.slice_to_cells(project, "camera", agent_slice,
        context_builder.full_voiceover(project, frames))
    expected_rows = [_cell_state(cell) for cell in expected]
    if not expected_rows or any(row["status"] != "ok" for row in expected_rows):
        errors.append("native_camera_cell_validation_failed")
    if any(not str(shot.get("цитата") or "").strip() for shot in agent_slice["shot_plan"]):
        errors.append("missing_camera_quote")
    if Counter(_json(r) for r in target) != Counter(_json(r) for r in expected_rows):
        errors.append("camera_cells_do_not_match_native_slice")
    return _result(proof, errors)


def verify_scene_assembly(project: Any, frames: Any, *, snapshot: Any, payload: Any) -> dict:
    """Check native coverage, registry and supported attrs, after camera_expand.

    Native registry expansion operates only on detached scalar copies to resolve
    scene/shot overrides. No writer, SQL, commit, export or checkpoint is called.
    """
    from app.services import db_apply
    from app.services.scene_design import assembler, context_builder
    from app.services.scene_design.runner import scene_design_enabled

    frames = list(frames) if frames is not None else None
    rows = [_frame_state(frame) for frame in frames or ()]
    registry = _meta(project).get("scene_registry")
    proof = {**_proof("sd_assemble", rows, snapshot, registry=registry),
             "entities_verified": False, "scene_space_verified": False,
             "checked_fields": {}}
    if not _snapshot_available(snapshot) or payload is None or frames is None:
        return _result(proof, unknown="captured assembly input, payload or output unavailable")
    if snapshot["enabled"] is not True or not scene_design_enabled(project):
        return _result(proof, unknown="scene design disabled; pass-through is not output proof")
    if proof["sha256"] is None:
        return _result(proof, unknown="assembly output cannot be structurally represented")
    errors = _frame_errors(project.id, rows, snapshot, camera=False)
    if (not isinstance(payload, dict) or not isinstance(payload.get("scenes"), list)
            or not payload["scenes"] or not isinstance(payload.get("ops"), list)
            or not payload["ops"] or _hash(payload) is None
            or any(not isinstance(sc, dict) for sc in payload["scenes"])
            or any(not isinstance(op, dict) or not isinstance(op.get("fields"), dict)
                   for op in payload["ops"])):
        return _result(proof, [*errors, "invalid_assembly_payload"])
    try:
        problems = assembler.validate_payload(project, frames, payload,
            context_builder.full_voiceover(project, frames))
    except (TypeError, ValueError, KeyError, AttributeError):
        return _result(proof, errors, unknown="native assembly validation unavailable")
    errors.extend("native_validation:" + problem for problem in problems)
    try:
        expected_registry = [db_apply._normalize_scene_card(sc) for sc in payload["scenes"]]
        if not isinstance(registry, list) or not registry:
            errors.append("missing_production_scene_registry")
        elif [db_apply._normalize_scene_card(sc) for sc in registry] != expected_registry:
            errors.append("production_scene_registry_does_not_match_payload")
    except (TypeError, ValueError, KeyError, AttributeError):
        return _result(proof, [*errors, "invalid_scene_registry"])
    scene_ids = [sc["id_scene"] for sc in expected_registry]
    if len(set(scene_ids)) != len(scene_ids):
        errors.append("duplicate_scene_registry_id")
    if errors:
        return _result(proof, errors)

    expected = [SimpleNamespace(**copy.deepcopy(row)) for row in snapshot["frames"]]
    expected_by_uuid = {frame.uuid: frame for frame in expected}
    wanted: dict[str, set[str]] = {}
    unsupported = set()
    try:
        for op in payload["ops"]:
            if op.get("target", "frame") != "frame":
                unsupported.add("target:" + str(op.get("target")))
                continue
            uid = str(op.get("frame_uuid") or "").strip()
            fields = db_apply.normalize_fields(op["fields"], db_apply.FIELD_ALIASES, scope="scene assembly")
            if not fields.get("shot01_id_scene"):
                errors.append("missing_frame_scene_binding:" + uid)
            attrs = dict(expected_by_uuid[uid].attrs or {})
            checks = wanted.setdefault(uid, {"shot01_id_scene"})
            for field, value in fields.items():
                if field in db_apply._ATTR_EXCEL_ROWS:
                    attrs[field] = str(value or "")
                    checks.add(field)
                elif field == "characters":
                    for key in ("characters", "persons", "персонажи"):
                        attrs[key] = str(value or "").strip()
                        checks.add(key)
                else:
                    unsupported.add(field)
            expected_by_uuid[uid].attrs = attrs
        # The original apply path expands scenes in Frame.number order.
        db_apply.expand_scene_registry_onto_frames(
            sorted(expected, key=lambda frame: frame.number), expected_registry, overwrite=True)
        actual = {frame.uuid: frame for frame in frames}
        for uid, fields in wanted.items():
            attrs = actual[uid].attrs
            if not isinstance(attrs, dict):
                errors.append("invalid_frame_attrs:" + uid)
                continue
            for field in sorted(fields):
                if attrs.get(field) != expected_by_uuid[uid].attrs.get(field):
                    errors.append("unapplied_frame_field:" + uid + ":" + field)
    except db_apply.ApplyOpsError as exc:
        return _result(proof, [*errors, "native_field_validation:" + str(exc)])
    except (TypeError, ValueError, KeyError, AttributeError):
        return _result(proof, errors, unknown="native assembly field check unavailable")
    proof["checked_fields"] = {uid: sorted(fields) for uid, fields in wanted.items()}
    return _result(proof, errors, unknown=("unsupported assembly field checks: " + ", ".join(sorted(unsupported)))
                   if unsupported else None)
