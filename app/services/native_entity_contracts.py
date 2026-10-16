from __future__ import annotations

from copy import deepcopy
import math
from pathlib import Path
from types import SimpleNamespace

from app.services.artifact_verifier import contained_path, verify_artifact
from app.services.excel_characters import characters_from_entities


_ENTITY_FIELDS = ("id", "project_id", "type", "code", "name", "sort_key", "attrs")
_ARTIFACT_FIELDS = ("id", "project_id", "frame_id", "kind", "uuid", "path", "meta")
_SCOPE_FIELDS = ("execution_id", "node_key", "attempt_id")


def hero_entity_contract():
    from app.services.node_capabilities import READ_ACTIONS

    return {
        "node_type": "hero", "artifact_kind": "hero_reference",
        "executor": "app.orchestrator.steps.generate_hero.run",
        "entity_executor": "app.orchestrator.steps.generate_hero._run_excel",
        "generation_executor": "app.orchestrator.steps.generate_hero._generate_one_excel_character",
        "parser": "app.services.excel_characters.characters_from_entities",
        "input_sources": ["entity", "xlsx"],
        "verified_input_source": "entity",
        "input_fields": list(_ENTITY_FIELDS),
        "phases": ["entity_extract", "model_prompt_if_base", "native_image_generation",
                   "artifact_apply", "batch_complete", "legacy_variation_hitl"],
        "outbound_image_generation": True,
        "entity_excel_hitl_required": False, "legacy_variation_hitl_required": True,
        "actions": READ_ACTIONS, "managed": False, "managed_eligible": False,
        "semantic_quality_verified": False, "business_commit_claim": False,
        "retry_policy": "original executor only",
        "verification": "native identity, supplied request/result bindings and actual local image",
        "provenance_authority": "caller must establish captured observation authenticity",
        "gen_id_semantics": "native generation attempt identity; not a durable provider job proof",
        "empty_entity_source": "unknown; XLSX, legacy input or native skip may still apply",
        "items": "deferred; original Project.item_descriptions and optional/error semantics differ",
    }


def items_entity_contract():
    from app.services.node_capabilities import READ_ACTIONS

    return {
        "node_type": "items", "artifact_kind": "item_reference",
        "executor": "app.orchestrator.steps.generate_items.run",
        "input_sources": ["Project.item_descriptions"],
        "outbound_image_generation": True, "optional_step": True,
        "hitl_required": False,
        "on_failure": "status rolls back to hero_ready; the operator edits the description and reruns",
        "actions": READ_ACTIONS, "managed": False, "managed_eligible": False,
        "semantic_quality_verified": False, "business_commit_claim": False,
        "retry_policy": "original executor only; a rerun submits paid image generation again",
        "write_authority": "existing worker only", "arbitrary_shell_code_paths": False,
    }


def _get(row, field):
    return row.get(field) if isinstance(row, dict) else getattr(row, field, None)


def _text(value):
    return str(value or "").strip()


def _identifier(value):
    return isinstance(value, str) and bool(value.strip())


def _positive_int(value):
    return type(value) is int and value > 0


def _digest(value):
    from app.services.observation import digest

    return digest(value)


def _entity_order(row):
    try:
        order = float(row["sort_key"] or 0)
    except (TypeError, ValueError, OverflowError):
        order = 0.0
    if not math.isfinite(order):
        order = 0.0
    return order, row["id"] if _positive_int(row["id"]) else 0, _text(row["code"])


def _characters(rows):
    parsed = characters_from_entities([SimpleNamespace(**row) for row in rows])
    identity = {}
    for row in rows:
        if _text(row["type"]) == "character":
            identity.setdefault(_text(row["code"]), row["id"])
    return [{**ch.to_dict(), "entity_id": identity.get(ch.id),
             "role": "reference_variation" if ch.ref_ids else "base"} for ch in parsed]


def hero_entity_input_snapshot(entities, *, project_id):
    rows = [deepcopy({field: _get(row, field) for field in _ENTITY_FIELDS})
            for row in entities or []]
    rows.sort(key=_entity_order)
    return {"project_id": project_id, "source": "entity", "entities": rows,
            "characters": _characters(rows)}


def _proof(snapshot):
    return {
        "node_type": "hero", "contract": "native_hero_entity_read",
        "status": "unknown", "structural_status": "unknown", "provenance_status": "unknown",
        "input_sha256": _digest(snapshot) if isinstance(snapshot, dict) else None,
        "output_sha256": None, "artifact_count": 0,
        "semantic_quality_verified": False, "business_commit_claim": False,
        "managed_eligible": False, "errors": [], "unknowns": [], "artifacts": [],
        "source": "caller supplied native rows and provenance; commit/journal acceptance is external",
    }


def _finish(proof, structural_errors, structural_unknowns, provenance_errors=(), provenance_unknowns=()):
    proof["structural_status"] = ("failed" if structural_errors else
                                  "unknown" if structural_unknowns else "passed")
    proof["provenance_status"] = ("failed" if provenance_errors else
                                  "unknown" if provenance_unknowns else "passed")
    proof["errors"] = list(dict.fromkeys([*structural_errors, *provenance_errors]))
    proof["unknowns"] = list(dict.fromkeys([*structural_unknowns, *provenance_unknowns]))
    proof["status"] = ("failed" if proof["errors"] else
                       "unknown" if proof["unknowns"] else "passed")
    proof["reason"] = next(iter(proof["errors"] or proof["unknowns"]), None)
    return proof


def _input_characters(snapshot, project_id, errors, unknowns):
    if (not isinstance(snapshot, dict) or
            not {"project_id", "source", "entities", "characters"} <= snapshot.keys() or
            not isinstance(snapshot["entities"], list) or
            not isinstance(snapshot["characters"], list)):
        unknowns.append("captured_input_unavailable_or_incomplete")
        return []
    if snapshot["source"] != "entity":
        unknowns.append("input_source_not_covered_by_entity_capture")
        return []
    rows = snapshot["entities"]
    if any(not isinstance(row, dict) or not set(_ENTITY_FIELDS) <= row.keys() for row in rows):
        unknowns.append("captured_entity_fields_incomplete")
        return []
    if (not _positive_int(project_id) or type(snapshot["project_id"]) is not int or
            snapshot["project_id"] != project_id):
        errors.append("input_project_outside_scope")
    ids = [row["id"] for row in rows]
    if any(not _positive_int(identity) for identity in ids):
        errors.append("invalid_entity_identity")
    if len({_text(identity) for identity in ids}) != len(ids):
        errors.append("duplicate_entity_identity")
    if any(type(row["project_id"]) is not int or row["project_id"] != project_id for row in rows):
        errors.append("entity_project_outside_scope")
    try:
        parsed = _characters([{field: row[field] for field in _ENTITY_FIELDS} for row in rows])
    except (TypeError, ValueError, OverflowError):
        unknowns.append("native_entity_parse_unavailable")
        return []
    codes = [ch["id"] for ch in parsed]
    if len(set(codes)) != len(codes):
        errors.append("duplicate_character_code")
    if snapshot["characters"] != parsed:
        errors.append("captured_character_identity_fields_or_roles_changed")
    if not parsed:
        unknowns.append("no_entity_targets_native_fallback_or_skip_not_captured")
    return parsed


def _latest_artifacts(artifacts, characters, project_id, errors):
    codes = {ch["id"] for ch in characters}
    latest, history = {}, {}
    row_ids, uuids = set(), set()
    for artifact in artifacts:
        row = deepcopy({field: _get(artifact, field) for field in _ARTIFACT_FIELDS})
        row["kind"] = getattr(row["kind"], "value", row["kind"])
        if type(row["project_id"]) is not int or row["project_id"] != project_id:
            errors.append("artifact_project_outside_scope")
        if row["frame_id"] is not None or row["kind"] != "hero_reference":
            errors.append("artifact_is_not_native_entity_hero_reference")
        if not _positive_int(row["id"]) or not _identifier(row["uuid"]):
            errors.append("invalid_artifact_identity")
            continue
        if row["id"] in row_ids or row["uuid"] in uuids:
            errors.append("duplicate_artifact_identity")
        row_ids.add(row["id"])
        uuids.add(row["uuid"])
        meta = row["meta"]
        code = meta.get("excel_id") if isinstance(meta, dict) else None
        if not _identifier(code) or code not in codes:
            errors.append("missing_or_foreign_artifact_character")
            continue
        history[row["uuid"]] = row
        if code not in latest or row["id"] > latest[code]["id"]:
            latest[code] = row
    return latest, history


async def _image(path, root):
    if not isinstance(path, (str, Path)) or not str(path).strip():
        return {"status": "failed", "reason": "missing_artifact_path"}
    try:
        return await verify_artifact(Path(path), root=root, kind="image")
    except (OSError, ValueError, TypeError) as exc:
        return {"status": "failed", "reason": "artifact_path_or_read_" + type(exc).__name__}


async def _disk_cache(code, root):
    try:
        path = contained_path(Path(root) / "characters" / (code + ".png"), root)
        if path.is_file() and path.stat().st_size >= 1000:
            return await _image(path, root)
    except (OSError, ValueError, TypeError):
        pass
    return None


def _binding(entry, expected, errors, unknowns):
    for key, value in expected.items():
        observed = entry.get(key)
        if observed is None or (isinstance(observed, str) and not observed.strip()):
            unknowns.append("provenance_field_unavailable:" + key)
        elif observed != value or type(observed) is not type(value):
            errors.append("provenance_field_conflict:" + key)


def _completion(section, *, name, identities, seen, errors, unknowns):
    if not isinstance(section, dict):
        unknowns.append(name + "_request_result_unavailable")
        return
    if section.get("complete") is not True:
        unknowns.append(name + "_completion_unproven")
    for field in (*identities, "sha256"):
        if not _identifier(section.get(field)):
            unknowns.append(name + "_field_unavailable:" + field)
    for field in identities:
        value = section.get(field)
        if not _identifier(value):
            continue
        key = (field, value)
        if key in seen:
            errors.append("duplicate_" + name + "_identity:" + field)
        seen.add(key)


def _result_provenance(entries, characters, latest, history, images, scope, input_hash, *, project_id, root):
    errors, unknowns = [], []
    if not isinstance(scope, dict) or any(not _identifier(scope.get(key)) for key in _SCOPE_FIELDS):
        return errors, ["execution_scope_unavailable_or_incomplete"]
    if entries is None:
        return errors, ["request_result_provenance_unavailable"]
    if not isinstance(entries, (list, tuple)):
        return errors, ["request_result_provenance_incomplete"]
    if not entries:
        return errors, ["request_result_provenance_unavailable"]
    by_code = {ch["id"]: ch for ch in characters}
    matched, artifact_seen, request_seen = set(), set(), set()
    for entry in entries:
        if not isinstance(entry, dict):
            unknowns.append("request_result_provenance_incomplete")
            continue
        code = entry.get("entity_code")
        if not _identifier(code):
            unknowns.append("provenance_field_unavailable:entity_code")
            continue
        if code not in by_code:
            errors.append("provenance_character_outside_scope")
            continue
        ch = by_code[code]
        _binding(entry, {**{key: scope[key] for key in _SCOPE_FIELDS},
            "project_id": project_id,
            "input_sha256": input_hash, "entity_id": ch["entity_id"],
            "entity_code": code, "role": ch["role"]}, errors, unknowns)
        uid = entry.get("artifact_uuid")
        if not _identifier(uid):
            unknowns.append("provenance_field_unavailable:artifact_uuid")
            continue
        if uid in artifact_seen:
            errors.append("duplicate_artifact_provenance")
        artifact_seen.add(uid)
        if uid not in history or history[uid]["meta"]["excel_id"] != code:
            errors.append("provenance_artifact_identity_conflict")
            continue
        if code not in latest or latest[code]["uuid"] != uid:
            continue
        matched.add(code)
        artifact = latest[code]
        generation = entry.get("generation")
        _completion(generation, name="generation", identities=("request_id", "gen_id"),
                    seen=request_seen, errors=errors, unknowns=unknowns)
        if isinstance(generation, dict):
            path = generation.get("file_path")
            if not isinstance(path, (str, Path)) or not str(path).strip():
                unknowns.append("generation_file_path_unavailable")
            else:
                try:
                    if contained_path(path, root=root) != contained_path(artifact["path"], root=root):
                        errors.append("generation_file_path_conflict")
                except (OSError, ValueError, TypeError):
                    errors.append("generation_file_path_conflict")
            if _identifier(generation.get("sha256")) and generation["sha256"] != images.get(code, {}).get("sha256"):
                errors.append("generation_file_hash_conflict")
            refs = generation.get("reference_artifact_uuids")
            if refs is None:
                unknowns.append("generation_reference_bindings_unavailable")
            elif (not isinstance(refs, list) or len(refs) != len(ch["ref_ids"]) or
                  any(not _identifier(uid) or uid not in history or
                      history[uid]["meta"]["excel_id"] != reference_code
                      for uid, reference_code in zip(refs, ch["ref_ids"]))):
                errors.append("generation_reference_bindings_conflict")
        model = entry.get("model")
        if ch["role"] == "reference_variation":
            if model is not None:
                errors.append("reference_variation_borrowed_model_completion")
        else:
            _completion(model, name="model", identities=("request_id", "response_id"),
                        seen=request_seen, errors=errors, unknowns=unknowns)
            prompt = artifact["meta"].get("hero_prompt")
            if not _identifier(prompt):
                unknowns.append("native_base_prompt_unavailable")
            elif isinstance(model, dict) and _identifier(model.get("sha256")) and model["sha256"] != _digest(prompt):
                errors.append("model_prompt_hash_conflict")
    for code in latest:
        if code not in matched:
            unknowns.append("current_artifact_provenance_unavailable:" + code)
    return errors, unknowns


async def verify_hero_entity_results(artifacts, *, project_id, snapshot, root, scope, provenance=None):
    proof = _proof(snapshot)
    errors, unknowns = [], []
    characters = _input_characters(snapshot, project_id, errors, unknowns)
    if errors or unknowns:
        return _finish(proof, errors, unknowns, provenance_unknowns=["output_not_verified"])
    if artifacts is None:
        return _finish(proof, [], ["native_artifact_rows_unavailable"],
                       provenance_unknowns=["output_not_verified"])
    latest, history = _latest_artifacts(artifacts, characters, project_id, errors)
    proof["artifact_count"] = len(latest)
    if errors:
        return _finish(proof, errors, [], provenance_unknowns=["output_not_verified"])
    images, output = {}, []
    for ch in characters:
        code = ch["id"]
        if code not in latest:
            cached = await _disk_cache(code, root) if not provenance else None
            if cached and cached["status"] == "passed":
                unknowns.append("disk_cache_without_artifact_provenance:" + code)
            else:
                errors.append("missing_native_artifact:" + code)
            continue
        artifact = latest[code]
        meta = artifact["meta"]
        if "excel_ref_ids" not in meta or "excel_used_refs" not in meta:
            unknowns.append("native_role_metadata_unavailable:" + code)
        elif (meta["excel_ref_ids"] != ch["ref_ids"] or
              meta["excel_used_refs"] is not bool(ch["ref_ids"])):
            errors.append("native_reference_role_or_ids_conflict:" + code)
        image = await _image(artifact["path"], root)
        images[code] = image
        if image["status"] == "failed":
            errors.append("native_image_invalid:" + code + ":" + str(image.get("reason")))
        elif image["status"] != "passed":
            unknowns.append("native_image_verification_unavailable:" + code)
        output.append({**artifact, "verification": image})
        proof["artifacts"].append({"entity_code": code, "artifact_uuid": artifact["uuid"],
                                   "verification": image})
    proof["output_sha256"] = _digest(output)
    provenance_errors, provenance_unknowns = _result_provenance(
        provenance, characters, latest, history, images, scope, proof["input_sha256"],
        project_id=project_id, root=root)
    return _finish(proof, errors, unknowns, provenance_errors, provenance_unknowns)
