from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OPERATION = "retry_local_worker"
CLOCK_TOLERANCE_S = 2.0


def spec_ok(node_type: str | None) -> bool:
    from app.orchestrator.node_spec import capability_spec_for
    from app.services import revisions
    spec = capability_spec_for(str(node_type))
    return bool(node_type in revisions.LOCAL_RECOVERY_NODES and spec is not None
                and spec.local_recovery and spec.effective_managed)


def ledger_summary_of(ids) -> dict[str, Any]:
    ordered = sorted(ids)
    return {"count": len(ordered), "sha256": hashlib.sha256("\n".join(ordered).encode()).hexdigest()}


async def ledger_summary(project_id: int) -> dict[str, Any]:
    from app.services import media_admission
    return ledger_summary_of(await media_admission.task_fingerprint(project_id))


def source_proof(facts) -> dict[str, Any] | None:
    proofs = [e for e in facts if e.kind == "node_result_verification"
              and (e.payload or {}).get("contract") == "native_media"]
    return (proofs[-1].payload or {}) if proofs else None


def check_source(facts) -> dict[str, Any]:
    from app.services.media_admission import MediaAdmissionDenied
    proof = source_proof(facts or [])
    if proof is None:
        raise MediaAdmissionDenied("source_proof_missing", "no media proof of the failed attempt")
    failure = proof.get("attempt_failure")
    if proof.get("status") != "failed" or not isinstance(failure, dict):
        raise MediaAdmissionDenied("source_proof_missing", "the proof does not type the attempt's own failure")
    if failure.get("retryable_by_node") is not True:
        raise MediaAdmissionDenied("failure_not_retryable", str(failure.get("error_type")))
    return {"error_type": failure.get("error_type"), "retryable_by_node": True}


async def admit(project, node_type: str, *, source_facts=None) -> dict[str, Any]:
    from app.services import media_admission, media_recovery, media_resume
    Denied = media_admission.MediaAdmissionDenied
    if not spec_ok(node_type):
        raise Denied("node_not_admitted", str(node_type))
    if source_facts is None:
        raise Denied("source_proof_missing", "evidence about the failed attempt is required")
    report = await media_recovery.assess(project, node_type)
    verdict = report["verdict"]
    if (verdict["mode"], verdict["code"]) != ("retry_same", "local_only_no_provider_spend") \
            or report.get("submits_new_paid_request") is not False:
        raise Denied("verdict_denied", str(verdict.get("code", "")))
    proof = check_source(source_facts)
    if not media_resume.enabled():
        raise Denied("ledger_unavailable", "provider ledger disabled: its stillness cannot be proven")
    try:
        before = await ledger_summary(project.id)
    except Exception as exc:
        raise Denied("ledger_unavailable", type(exc).__name__) from exc
    return {"operation": OPERATION, "verdict": verdict, "report": report, "ledger_before": before,
            "source_proof": proof}



def _claimed_ts(row) -> float | None:
    text = (row.result or {}).get("claimed_at")
    try:
        return datetime.fromisoformat(str(text)).replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return None


async def _verify_assemble(session, project, row, facts) -> dict[str, Any]:
    from sqlalchemy import select
    from app.models import Artifact, ArtifactKind, HITLKind, HITLRequest
    from app.services import media_verification

    root = Path(project.data_dir)
    checks: dict[str, Any] = {}
    art = await session.scalar(select(Artifact).where(
        Artifact.project_id == project.id, Artifact.kind == ArtifactKind.final_video)
        .order_by(Artifact.id.desc()).limit(1))
    path = Path(art.path) if art is not None and art.path else None
    present = bool(path is not None and path.is_file())
    checks["artifact_row"] = present
    inside = False
    if present:
        try:
            path.resolve().relative_to(root.resolve())
            inside = True
        except (ValueError, OSError):
            inside = False
    checks["file_inside_project"] = inside
    claimed = _claimed_ts(row)
    checks["claimed_at_known"] = claimed is not None
    checks["produced_after_claim"] = bool(
        present and claimed is not None and path.stat().st_mtime >= claimed - CLOCK_TOLERANCE_S)
    probe: dict[str, Any] = {}
    if present and inside:
        probe = await media_verification._probe(path, root=root, kind="assembly")
    sha = probe.get("sha256")
    checks["verify_artifact_passed"] = probe.get("status") == "passed" and bool(sha)
    checks["digest_matches_artifact"] = bool(sha) and sha == ((art.meta or {}).get("sha256") if art else None)
    requests = list((await session.scalars(select(HITLRequest).where(
        HITLRequest.project_id == project.id, HITLRequest.kind == HITLKind.approve_final))).all())
    mine = [r for r in requests if sha and (r.payload or {}).get("sha256") == sha]
    pending = [r for r in mine if getattr(r.decision, "value", r.decision) == "pending"]
    checks["approve_final_pending"] = bool(pending)
    proofs = [e for e in facts if e.kind == "node_result_verification"
              and (e.payload or {}).get("contract") == "native_media"]
    last = (proofs[-1].payload or {}) if proofs else {}
    items = last.get("items") or []
    checks["journal_proof_matches"] = bool(
        sha and last.get("status") == "passed" and items and items[0].get("sha256") == sha)
    from app.services import media_admission
    try:
        after = ledger_summary_of(await media_admission.task_fingerprint(project.id))
    except Exception as exc:
        after = {"error": type(exc).__name__}
    before = (row.preconditions or {}).get("ledger_before")
    checks["ledger_unchanged"] = bool(before) and after == before
    guards = [e for e in facts if e.kind == "local_recovery_guard"]
    guard = (guards[-1].payload or {}) if guards else {}
    checks["no_paid_seam_hit"] = bool(guard.get("resume_only") is True and guard.get("paid_seam_hits") == 0)
    checks["no_model_requests"] = not any(e.kind in {"model_start", "model_end", "model_error"} for e in facts)
    final_dir = root / "final"
    checks["no_part_files"] = not (final_dir.is_dir() and any(final_dir.glob(".*.part.mp4")))
    details = {"sha256": sha, "probe_status": probe.get("status"), "probe_reason": probe.get("reason"),
               "approve_final": {"requests_for_this_file": len(mine), "pending": len(pending),
                                 "human_barrier": "approve_final", "approved_by_supervisor": False},
               "ledger_before": before, "ledger_after": after,
               "paid_seam_hits": guard.get("paid_seam_hits"),
               "project_status": getattr(getattr(project, "status", None), "value", None)}
    required = ("artifact_row", "file_inside_project", "claimed_at_known", "produced_after_claim",
                "verify_artifact_passed", "digest_matches_artifact", "approve_final_pending",
                "journal_proof_matches", "ledger_unchanged", "no_paid_seam_hit", "no_model_requests",
                "no_part_files")
    return {**checks, "local_details": details, "local_required_checks": list(required),
            "result_confirmed": all(checks[k] for k in required), "local_verifier": "assemble"}


_VERIFIERS = {"assemble": _verify_assemble}


async def verify(session, project, row, facts) -> dict[str, Any]:
    node_type = (row.preconditions or {}).get("effective_node_type")
    verifier = _VERIFIERS.get(str(node_type))
    if verifier is None or project is None or not spec_ok(node_type):
        return {"result_confirmed": False, "local_verifier": None, "reason": "no local verifier for this node"}
    return await verifier(session, project, row, facts)
