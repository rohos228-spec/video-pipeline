from types import SimpleNamespace

from app.services.diagnosis import RECOMMENDATIONS, TITLES, derive_incidents

SCOPE = SimpleNamespace(execution_id="execution", project_id=2, node_keys=["anim", "scene"],
                        observer_state="active", gaps=[])
_n = [0]


def ev(kind, /, *, node="anim", attempt="a1", request=None, **payload):
    _n[0] += 1
    return dict(id=_n[0], event_id=f"de{_n[0]}", execution_id="execution", project_id=2, node_key=node,
                attempt_id=attempt, request_id=request, kind=kind, payload=payload, db_scope="project",
                created_at="2026-10-06 12:00:00")


def degraded(reason, shot, numbers):
    return ev("animation_degraded", contract="native_animation_prompts", reason=reason, shot=shot,
              frame_numbers=numbers, frame_count=len(numbers), cause_class="GptApiError",
              business_commit_claim=False, semantic_quality_verified=False)


def test_animation_local_fallback_is_a_visible_nonblocking_warning_for_a_human():
    (item,) = derive_incidents(SCOPE, [degraded("local_fallback", 1, [3, 4])], {})
    assert item["type"] == "animation_degraded" and item["title"] == TITLES["animation_degraded"]
    assert (item["blocking"], item["severity"], item["status"]) == (False, "warning", "human_review")
    assert "локальным шаблоном" in item["fact"] and "3, 4" in item["fact"]
    assert item["degradation"] == "local_fallback" and item["semantic_quality_verified"] is False
    assert item["recommendation"]["automatic_action"] is False
    assert item["recommendation"]["title"] == RECOMMENDATIONS["human_review"]


def test_animation_skipped_second_shot_is_its_own_incident_with_its_own_text():
    out = derive_incidents(SCOPE, [degraded("local_fallback", 1, [1]), degraded("shot2_skipped", 2, [1, 2])], {})
    assert sorted(i["degradation"] for i in out) == ["local_fallback", "shot2_skipped"]
    skipped = next(i for i in out if i["degradation"] == "shot2_skipped")
    assert "shot_02" in skipped["fact"] and "не созданы" in skipped["fact"]
    assert all(i["blocking"] is False for i in out)


def test_animation_degradation_is_never_a_recovery_incident():
    from app.services.decision_v1 import RECOVERY_INCIDENTS
    assert "animation_degraded" not in RECOVERY_INCIDENTS
    assert "animation_degraded" in TITLES


def scene_proof(**kw):
    base = dict(contract="native_scene_agent", agent="characters", status="failed", failure_class="contract_rejected",
                reason="invalid_characters_slice:bad_id_pattern:hero", errors=["invalid_characters_slice:bad_id_pattern:hero"])
    return ev("node_result_verification", node="scene", **{**base, **kw})


def test_a_failed_scene_result_check_is_a_contract_rejected_incident_with_its_failure_class():
    (item,) = derive_incidents(SCOPE, [scene_proof()], {})
    assert item["type"] == "contract_rejected" and item["status"] == "human_review"
    assert item["failure_class"] == "contract_rejected" and "characters" in item["fact"]
    assert item["semantic_quality_verified"] is False
    assert "Автоповтор не выполняется" in item["fact"]


def test_scene_result_check_that_passed_or_is_unknown_raises_nothing():
    assert derive_incidents(SCOPE, [scene_proof(status="passed", failure_class=None, errors=[])], {}) == []
    assert derive_incidents(SCOPE, [scene_proof(status="unknown", failure_class=None, errors=[])], {}) == []


def test_a_typed_scene_parse_failure_is_one_json_incident_not_two():
    rows = [ev("model_start", node="scene", request="r1"), ev("model_end", node="scene", request="r1", complete=True),
            ev("parse_result", node="scene", request="r1", contract="native_scene_agent", agent="world",
               syntax_valid=True, parsed=False, payload_required=True, payload_present=False,
               failure_class="missing_payload", schema_valid=False),
            ev("runtime_check", node="scene", request="r1", check="native_scene_agent_parse", status="failed",
               performed=True, failure_class="missing_payload")]
    out = derive_incidents(SCOPE, rows, {})
    assert [i["type"] for i in out] == ["missing_payload"]


def test_a_contract_rejected_scene_parse_stays_a_rejected_check_not_a_json_incident():
    rows = [ev("model_start", node="scene", request="r1"), ev("model_end", node="scene", request="r1", complete=True),
            ev("parse_result", node="scene", request="r1", contract="native_scene_agent", agent="action",
               syntax_valid=False, parsed=True, payload_required=True, payload_present=True,
               failure_class="contract_rejected", schema_valid=False),
            ev("runtime_check", node="scene", request="r1", check="native_scene_agent_parse", status="failed",
               performed=True, failure_class="contract_rejected")]
    assert [i["type"] for i in derive_incidents(SCOPE, rows, {})] == ["contract_rejected"]
