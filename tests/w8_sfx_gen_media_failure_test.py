"""sfx_gen: сбой типизируется как media_failure (как у остальных медиа-нод), нода остаётся под наблюдением.

Чистые проверки без сети и БД. Класс доказательства: U.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import node_capabilities
from app.services.diagnosis import derive_incidents
from app.services.sfx_plan import _type_sfx_gen_as_media

SCOPE = SimpleNamespace(execution_id="execution", project_id=2, node_keys=["sfx"], observer_state="active", gaps=[])
_n = [0]


def _ev(kind, **payload):
    _n[0] += 1
    return dict(id=_n[0], event_id=f"pe{_n[0]}", execution_id="execution", project_id=2, node_key="sfx",
                attempt_id="a1", request_id=None, kind=kind, payload=payload, db_scope="project",
                created_at="2026-10-09 12:00:00")


def _raw(status, **kw):
    return {"status": status, "reason": "committed native sfx result unavailable", "errors": ["ValueError"],
            "error_type": "ValueError", "input_sha256": "x", "files": [], "managed_eligible": False,
            "business_commit_claim": False, "semantic_quality_verified": False, "mix_verified": False, **kw}


def test_failed_proof_becomes_a_blocking_media_failure():
    proof = _type_sfx_gen_as_media(_raw("failed"), None)
    assert proof["contract"] == "native_media" and proof["node_type"] == "sfx_gen"
    (item,) = derive_incidents(SCOPE, [_ev("node_result_verification", **proof)], {})
    assert (item["type"], item["blocking"], item["status"], item["media"]) == ("media_failure", True, "human_review", True)
    assert item["fact"].startswith("Звуки:")


def test_unknown_proof_is_a_non_blocking_media_failure_warning():
    proof = _type_sfx_gen_as_media(_raw("unknown", reason="captured native sfx input/body unavailable"), None)
    (item,) = derive_incidents(SCOPE, [_ev("node_result_verification", **proof)], {})
    assert (item["type"], item["blocking"], item["severity"]) == ("media_failure", False, "warning")


def test_passed_proof_is_silent():
    proof = _type_sfx_gen_as_media(_raw("passed", reason=None, files=[{"idx": 0}, {"idx": 1}]), None)
    assert proof["items_total"] == 2 and "проверено файлов 2" in proof["summary"]
    assert derive_incidents(SCOPE, [_ev("node_result_verification", **proof)], {}) == []


def test_attempt_exception_overrides_a_pass_and_is_not_repeatable_by_the_node():
    proof = _type_sfx_gen_as_media(_raw("passed", reason=None, files=[{"idx": 0}]), RuntimeError("boom"))
    assert proof["status"] == "failed" and proof["reason"] == "attempt_failed:RuntimeError"
    assert proof["attempt_failure"] == {"error_type": "RuntimeError", "retryable_by_node": False,
                                        "new_verified_artifact": False}
    (item,) = derive_incidents(SCOPE, [_ev("node_result_verification", **proof)], {})
    assert item["type"] == "media_failure" and item["blocking"] is True


def test_media_failure_is_present_next_to_the_plain_node_error():
    proof = _type_sfx_gen_as_media(_raw("failed"), RuntimeError("x"))
    rows = [_ev("node_result_verification", **proof), _ev("node_error", error_type="RuntimeError")]
    assert "media_failure" in [i["type"] for i in derive_incidents(SCOPE, rows, {})]


def test_sfx_gen_is_not_promoted_and_not_admitted_by_the_local_recovery():
    from app.services import local_node_recovery, media_admission
    from app.orchestrator import node_spec
    spec = node_spec.capability_spec_for("sfx_gen")
    assert spec.recovery_only is True  # как было: только supervised-возобновление, не свободный запуск
    assert spec.verifier == "sfx_result"
    assert "sfx_gen" not in media_admission.ADMITTED_NODES
    proof = _type_sfx_gen_as_media(_raw("failed"), RuntimeError("x"))
    facts = [SimpleNamespace(kind="node_result_verification", payload=proof)]
    with pytest.raises(media_admission.MediaAdmissionDenied) as err:
        local_node_recovery.check_source(facts)
    assert err.value.code == "failure_not_retryable"
