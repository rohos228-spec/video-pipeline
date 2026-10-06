"""Supervised re-run of a node that has NO model request and NO provider job (today: assemble).

Text nodes are recovered by re-dispatching the original worker once; their ticket is verified from model facts
(applied data, parsed payload). A local node has neither, and a paid media node must never be re-run. This module
is the third kind: a local node is re-run once by the SAME worker dispatch, and everything that makes it safe or
verified is independent of the node's own claims:

* admission (before any ticket exists, read-only):
  - the node declares ``local_recovery`` and is accepted (CapabilitySpec);
  - the static assessment says no provider spend (``media_recovery.assess``);
  - the failed attempt left a typed media proof of ITS OWN outcome (``attempt_failure``) and the node itself
    treats that failure as repeatable (ffmpeg, failed verification, a file error). An input error (no clips,
    no voice) is deterministic: a re-run cannot change it, so it stays with a human;
  - the provider task ledger can be read: its fingerprint is stored in the ticket;
* the attempt (``observation.observe_attempt``): runs inside ``media_spend.resume_only``, so every seam that
  would send a paid request refuses before a byte leaves and is counted (``local_recovery_guard`` fact);
* verification (``reconcile``): from facts re-read by the supervisor, never from the node's return value:
  the final file is NEWER than the claim, ``verify_artifact`` passes again, its digest equals the artifact row,
  the ``approve_final`` request of that file exists and is still ``pending`` (the human barrier stands),
  the provider ledger is exactly as before, the guard counted zero refused paid requests, the attempt made no
  model request, and no part file is left behind.

Nothing here approves a HITL, starts a node, or touches a provider.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OPERATION = "retry_local_worker"
# A file written by the attempt cannot be older than the claim; one second of clock/filesystem granularity.
CLOCK_TOLERANCE_S = 2.0


def spec_ok(node_type: str | None) -> bool:
    """The node is in the local set, declares local_recovery and is an accepted (managed) capability."""
    from app.orchestrator.node_spec import capability_spec_for
    from app.services import revisions
    spec = capability_spec_for(str(node_type))
    return bool(node_type in revisions.LOCAL_RECOVERY_NODES and spec is not None
                and spec.local_recovery and spec.effective_managed)


def ledger_summary_of(ids) -> dict[str, Any]:
    ordered = sorted(ids)
    return {"count": len(ordered), "sha256": hashlib.sha256("\n".join(ordered).encode()).hexdigest()}


async def ledger_summary(project_id: int) -> dict[str, Any]:
    """Count and digest of every provider task id and submit intent the ledger holds for the project."""
    from app.services import media_admission
    return ledger_summary_of(await media_admission.task_fingerprint(project_id))


def source_proof(facts) -> dict[str, Any] | None:
    """The typed media proof of the failed attempt (last ``node_result_verification`` of the native_media contract)."""
    proofs = [e for e in facts if e.kind == "node_result_verification"
              and (e.payload or {}).get("contract") == "native_media"]
    return (proofs[-1].payload or {}) if proofs else None


def check_source(facts) -> dict[str, Any]:
    """Raise MediaAdmissionDenied unless the failed attempt proved its own repeatable failure."""
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
    """Read-only. The admitted operation, or a typed refusal. Never starts anything."""
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
    except Exception as exc:  # noqa: BLE001 - an unreadable ledger is a refusal, never a pass
        raise Denied("ledger_unavailable", type(exc).__name__) from exc
    return {"operation": OPERATION, "verdict": verdict, "report": report, "ledger_before": before,
            "source_proof": proof}


# ------------------------------------------------------------------------------------------------------------
# verification
# ------------------------------------------------------------------------------------------------------------

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
    # THIS attempt wrote the file: a file older than the claim is the earlier final, whatever its content is.
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
    # the journal of the attempt itself agrees with the independent read (it is a second witness, not the first)
    proofs = [e for e in facts if e.kind == "node_result_verification"
              and (e.payload or {}).get("contract") == "native_media"]
    last = (proofs[-1].payload or {}) if proofs else {}
    items = last.get("items") or []
    checks["journal_proof_matches"] = bool(
        sha and last.get("status") == "passed" and items and items[0].get("sha256") == sha)
    from app.services import media_admission
    try:
        after = ledger_summary_of(await media_admission.task_fingerprint(project.id))
    except Exception as exc:  # noqa: BLE001
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
    # journal_proof_matches: the attempt's own result hook (a second witness) recorded a passed proof for the same
    # digest; it must agree with what was just re-read, otherwise a human looks.
    required = ("artifact_row", "file_inside_project", "claimed_at_known", "produced_after_claim",
                "verify_artifact_passed", "digest_matches_artifact", "approve_final_pending",
                "journal_proof_matches", "ledger_unchanged", "no_paid_seam_hit", "no_model_requests",
                "no_part_files")
    return {**checks, "local_details": details, "local_required_checks": list(required),
            "result_confirmed": all(checks[k] for k in required), "local_verifier": "assemble"}


_VERIFIERS = {"assemble": _verify_assemble}


async def verify(session, project, row, facts) -> dict[str, Any]:
    """Independent verification of a local re-run ticket. Fail-closed: no verifier or no project = not confirmed."""
    node_type = (row.preconditions or {}).get("effective_node_type")
    verifier = _VERIFIERS.get(str(node_type))
    if verifier is None or project is None or not spec_ok(node_type):
        return {"result_confirmed": False, "local_verifier": None, "reason": "no local verifier for this node"}
    return await verifier(session, project, row, facts)
