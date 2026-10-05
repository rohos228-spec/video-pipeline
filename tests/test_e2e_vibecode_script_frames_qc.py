"""Offline guards for scripts/e2e_vibecode_script_frames_qc.py (pure helpers; the run itself starts the whole app)."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).parent.parent / "scripts" / "e2e_vibecode_script_frames_qc.py"
_spec = importlib.util.spec_from_file_location("e2e_vibecode_script_frames_qc", _PATH)
script = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = script
_spec.loader.exec_module(script)

FRAME = {"uuid": "a" * 24, "number": 1, "voiceover_text": script.FULL_VO, "attrs": {}}


# ---------- safety: dry only, loopback only ----------

def test_default_mode_is_dry_and_there_is_no_live_flag():
    args = script._args(["--work-dir", "w"])
    assert args.dry is True and args.break_node == "script" and args.break_mode == "malformed"
    assert not hasattr(args, "live")
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--live"])


def test_undercount_only_for_shots():
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--break-node", "script", "--break-mode", "undercount"])
    assert script._args(["--work-dir", "w", "--break-node", "shots", "--break-mode", "undercount"]).break_mode == "undercount"


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]"])
def test_loopback_hosts_are_allowed(host):
    assert script.refusal_reason("POST", host) is None


@pytest.mark.parametrize("host", ["vibecode.moe", "api.kie.ai", "example.com", "10.0.0.5", "", None, "127.0.0.1.evil.com"])
def test_every_non_loopback_host_is_refused(host):
    assert "refused" in script.refusal_reason("GET", host)


def test_environment_is_synthetic_loopback_without_relay(tmp_path):
    env = script.env_overrides(tmp_path, 18766)
    assert env["VIBECODE_BASE_URL"] == "http://127.0.0.1:18766/v1"
    assert env["GPT_BASE_URL"].startswith("http://127.0.0.1:")
    assert env["VIBECODE_API_KEY"].startswith("synthetic") and env["TEXT_LLM_PROVIDER"] == "vibecode"
    assert not any(k in env for k in ("GPT_RELAY_TOKEN", "GPT_PROXY_URL"))


def test_route_predicates():
    assert script.is_chat_completions("/v1/chat/completions") and script.is_chat_completions("/chat/completions/")
    assert script.is_kie_responses("/codex/v1/responses") and not script.is_kie_responses("/v1/chat/completions")


def test_node_suffix_maps_the_group_nodes():
    assert [script.node_suffix(k) for k in script.node_keys()] == list(script.node_suffixes())
    assert script.node_suffix("n_script") is None and script.node_suffix(None) is None


def test_linear_edges_chain_the_group_nodes():
    edges = script._linear_edges()
    assert [(e["source"], e["target"]) for e in edges] == list(zip(script.node_keys(), script.node_keys()[1:]))


# ---------- the Vibecode wire format is what the real parser reads ----------

def test_sse_body_is_parsed_by_the_real_vibecode_parser():
    from app.services.gpt_api import parse_chat_completions_sse_lines
    body = script.sse_chat_body('{"ops": []}', "r1").decode()
    text, finish, last = parse_chat_completions_sse_lines(body.splitlines())
    assert text == '{"ops": []}' and finish == "stop"
    assert "status" not in last  # chat/completions carries no Kie-style status


def test_sse_usage_only_trailing_chunk_and_status_flag():
    from app.services.gpt_api import parse_chat_completions_sse_lines
    body = script.sse_chat_body("hello world", "r2", style="stream-usage").decode()
    text, finish, last = parse_chat_completions_sse_lines(body.splitlines())
    assert text == "hello world" and finish == "stop" and last["choices"] == [] and "usage" in last
    with_status = script.sse_chat_body("x", "r3", status_completed=True).decode()
    assert '"status": "completed"' in with_status


def test_json_body_is_parsed_by_parse_choice():
    from app.services.gpt_api import _parse_choice
    assert _parse_choice(json.loads(script.chat_json_body("ответ", "r4"))) == ("ответ", "stop")


def test_request_text_reads_string_and_part_contents():
    body = {"messages": [{"role": "system", "content": "s"},
                         {"role": "user", "content": [{"type": "text", "text": "u"}]}]}
    assert script.request_text(body) == "s\nu"


def test_pack_for_request_matches_24_hex_uuids_not_rfc_uuids():
    other = {**FRAME, "uuid": "b" * 24, "number": 2}
    assert script.pack_for_request([FRAME, other], f"db_frames: {'b' * 24}") == [other]
    assert script.pack_for_request([FRAME, other], "no ids here") == [FRAME, other]


# ---------- Decision stub speaks the real proposal contract ----------

def _context(allowed):
    return {"allowed_actions": allowed, "context_version": "v1", "evidence_ids": ["e1", "e2"]}


def test_decision_context_is_found_inside_a_message_envelope():
    ctx = _context(["read_events"])
    text = "SYSTEM\n" + json.dumps({"noise": 1}) + "\nuser: " + json.dumps(ctx)
    assert script.decision_context(text) == ctx
    assert script.decision_context("nothing") is None


def test_decision_turns_pass_the_real_validator():
    from app.services.decision_v1 import validate_proposal
    ctx = _context(["read_events", "retry_same", "finish", "wait", "human_review"])
    actions = []
    for turn in (1, 2, 3):
        proposal = script.decision_proposal(turn, ctx)
        validate_proposal(json.dumps(proposal), ctx)
        actions.append(proposal["action"])
    assert actions == ["read_events", "retry_same", "finish"]


def test_decision_falls_back_to_human_review_when_retry_is_not_allowed():
    proposal = script.decision_proposal(2, _context(["read_events", "human_review", "finish"]))
    assert proposal["action"] == "human_review" and proposal["arguments"] == {}


def test_runtime_config_is_a_valid_loopback_vibecode_production_config():
    from app.services import decision_config
    cfg = decision_config.validate_config(script.runtime_config(18766))
    assert decision_config.provider(cfg) == "vibecode" and cfg["profiles"] == {}
    assert cfg["endpoint"] == "http://127.0.0.1:18766/v1/chat/completions"
    assert "retry_profile" not in cfg["allowed_actions"] and cfg["continuation_allowed"] is False


# ---------- model fixtures ----------

def test_valid_answers_follow_the_space_scenario():
    bits = json.loads(script.valid_answer("script", [FRAME]))["ops"][0]["fields"]["биты"]
    assert bits == script.BITS
    action = json.loads(script.valid_answer("action", [FRAME]))["ops"][0]["fields"]
    assert action == {"площадка": script.PLAN}  # the site node writes only the site (no main action)
    shots = json.loads(script.valid_answer("shots", [FRAME]))["ops"][0]["fields"]["кадры"]
    assert len(shots) == sum(len(s["shots"]) for s in script.SCENES)
    assert json.loads(script.valid_answer("qc", [FRAME])) == {"ops": []}


def test_qc_edit_answer_changes_only_the_street_scene():
    frame = {**FRAME, "voiceover_text": "Он бежал по улице, не разбирая дороги.",
             "attrs": {"кадры": script._kadry(1)}}
    ops = json.loads(script.valid_answer("qc", [frame], qc_answer="edit"))["ops"]
    assert len(ops) == 1 and len(ops[0]["fields"]["кадры"]) == 1


def test_broken_answers_are_unusable_for_the_real_parser():
    from app.services.db_apply import extract_apply_ops_json
    assert extract_apply_ops_json(script.broken_answer("malformed", "script", [FRAME])) is None
    cut = script.broken_answer("truncated", "script", [FRAME])
    with pytest.raises(ValueError):
        json.loads(cut)
    from app.services.gpt_api import looks_truncated_llm_text
    assert looks_truncated_llm_text(cut) and not looks_truncated_llm_text(script.MALFORMED)


def test_undercount_is_valid_json_with_no_shots():
    data = json.loads(script.broken_answer("undercount", "shots", [FRAME]))
    full = json.loads(script.valid_answer("shots", [FRAME]))
    assert data["ops"][0]["fields"]["кадры"] == [] and full["ops"][0]["fields"]["кадры"]


# ---------- assertions ----------

def _happy_snapshot():
    target = "n_excel_gpt_fw_script"
    return {
        "counters": {"worker_total": 20, "worker_recovery": 1, "decision": 2, "kie_responses": 0},
        "refused_hosts": [],
        "diagnoses": {"incidents": [{"type": "invalid_json", "node_key": target, "status": "resolved_confirmed",
                                     "evidence_incomplete": False}]},
        "incident_seen": {"i1": {"type": "invalid_json", "node_key": target, "evidence_incomplete": False}},
        "decisions": {"records": [{"status": "done", "model_steps": 2}]},
        "actions": {"recoveries": [{"status": "verified", "verification_result": {"verified": True}}]},
        "node_status": {k: "done" for k in script.node_keys()},
        "events": [{"kind": "model_start", "node_key": target, "request_id": "r1", "payload": {}},
                   {"kind": "model_end", "node_key": target, "request_id": "r1", "payload": {"complete": True}},
                   {"kind": "parse_result", "node_key": target, "request_id": "r1", "payload": {}},
                   {"kind": "runtime_check", "node_key": "n_excel_gpt_fw_qc",
                    "payload": {"check": "qc_noop", "status": "passed"}}],
        "hitl_pending": [],
    }


def test_happy_snapshot_passes_every_check():
    ok, failed = script.verdict(script.evaluate(_happy_snapshot(), break_node="script", break_mode="malformed"))
    assert ok and failed == []


def test_each_design_requirement_has_a_failing_case():
    cases = {
        "incident_invalid_json": lambda s: s["diagnoses"].update(incidents=[]),
        "journal_model_end_complete_on_chat_completions": lambda s: s["events"][1]["payload"].update(complete=False),
        "parse_result_linked_to_own_model_request": lambda s: s["events"][2].update(request_id="other-pack"),
        "qc_noop_accepted_without_status": lambda s: s["events"].pop(3),
        "decision_done_with_retry_same": lambda s: s["decisions"].update(records=[{"status": "human_review", "model_steps": 2}]),
        "recovery_verified": lambda s: s["actions"].update(recoveries=[{"status": "human_review"}]),
        "target_node_done": lambda s: s["node_status"].update(n_excel_gpt_fw_script="failed"),
        "chain_continued_all_nodes_done": lambda s: s["node_status"].update(n_excel_gpt_fw_report="pending"),
        "hitl_none_pending": lambda s: s.update(hitl_pending=[("script", None)]),
        "no_provider_host_attempt": lambda s: s.update(refused_hosts=["vibecode.moe"]),
        "only_vibecode_chat_completions": lambda s: s["counters"].update(kie_responses=1),
        "recovery_request_on_vibecode_profile": lambda s: s["counters"].update(worker_recovery=0),
    }
    for name, mutate in cases.items():
        snap = _happy_snapshot()
        mutate(snap)
        ok, failed = script.verdict(script.evaluate(snap, break_node="script", break_mode="malformed"))
        assert not ok and name in failed, (name, failed)


def test_failed_checks_name_who_must_fix_them():
    snap = _happy_snapshot()
    snap["events"][1]["payload"]["complete"] = False
    checks = {c["name"]: c for c in script.evaluate(snap, break_node="script", break_mode="malformed")}
    assert checks["journal_model_end_complete_on_chat_completions"]["needs"]


def test_undercount_expects_contract_rejected_no_recovery_and_a_rule_on_node_error():
    target = "n_excel_gpt_fw_shots"
    snap = {"counters": {"worker_total": 5, "decision": 1, "kie_responses": 0}, "refused_hosts": [],
            "diagnoses": {"incidents": [{"type": "contract_rejected", "node_key": target, "status": "human_review"}]},
            "actions": {"recoveries": []}, "decisions": {"records": []},
            "events": [{"kind": "node_error", "node_key": target, "payload": {"error_type": "PassStopError", "rule": "R-SHOT-COVERAGE"}}]}
    ok, failed = script.verdict(script.evaluate(snap, break_node="shots", break_mode="undercount"))
    assert ok, failed
    snap["events"][0]["payload"].pop("rule")
    ok, failed = script.verdict(script.evaluate(snap, break_node="shots", break_mode="undercount"))
    assert not ok and failed == ["node_error_carries_rule"]


def test_truncated_answer_expects_hold_single_post_and_no_recovery():
    target = "n_excel_gpt_fw_shots"
    snap = {"counters": {"worker_total": 4, "decision": 0, "kie_responses": 0, "by_node": {target: 1}},
            "refused_hosts": [], "actions": {"held": True, "recoveries": []}, "decisions": {"records": []},
            "diagnoses": {"incidents": [{"type": "api_error", "node_key": target, "status": "human_review"}]},
            "node_status": {target: "queued"}, "events": []}
    ok, failed = script.verdict(script.evaluate(snap, break_node="shots", break_mode="truncated"))
    assert ok, failed
    snap["counters"]["by_node"][target] = 2
    snap["actions"]["held"] = False
    _ok, failed = script.verdict(script.evaluate(snap, break_node="shots", break_mode="truncated"))
    assert {"truncated_answer_posted_once", "truncated_answer_holds_execution"} <= set(failed)


# ---------- retry_profile scenario ----------

def test_recovery_flag_defaults_to_retry_same_and_profile_needs_malformed():
    assert script._args(["--work-dir", "w"]).recovery == "retry_same"
    assert script._args(["--work-dir", "w", "--recovery", "retry_profile"]).recovery == "retry_profile"
    with pytest.raises(SystemExit):
        script._args(["--work-dir", "w", "--recovery", "retry_profile", "--break-node", "shots", "--break-mode", "undercount"])


def test_profile_matches_the_owner_example_config():
    example = json.loads((Path(__file__).parent.parent / "config" / "orchestrator-runtime.vibecode.example.json")
                         .read_text(encoding="utf-8"))
    assert example["profiles"] == {script.PROFILE_ID: script.PROFILE}


def test_profile_runtime_config_is_valid_and_vibecode_only():
    from app.services import decision_config
    cfg = decision_config.validate_config(script.runtime_config(18766, recovery="retry_profile"))
    assert list(cfg["profiles"]) == [script.PROFILE_ID] and "retry_profile" in cfg["allowed_actions"]
    assert cfg["profiles"][script.PROFILE_ID]["provider"] == "vibecode"
    plain = decision_config.validate_config(script.runtime_config(18766))
    assert plain["profiles"] == {} and "retry_profile" not in plain["allowed_actions"]


def test_decision_picks_the_eligible_or_approved_vibecode_profile():
    from app.services.decision_v1 import validate_proposal
    base = {"allowed_actions": ["read_events", "retry_profile", "human_review", "finish"],
            "context_version": "v1", "evidence_ids": ["e1"]}
    eligible = {**base, "eligible_profiles": [{"profile_id": script.PROFILE_ID}], "approved_profiles": []}
    proposal = script.decision_proposal(2, eligible, "retry_profile")
    assert proposal["action"] == "retry_profile" and proposal["arguments"] == {"profile_id": script.PROFILE_ID}
    validate_proposal(json.dumps(proposal), eligible)
    approved = {**base, "eligible_profiles": [], "approved_profiles": [{"overlay_id": "o1", "profile_id": script.PROFILE_ID}]}
    assert script.decision_proposal(2, approved, "retry_profile")["arguments"] == {"overlay_id": "o1"}
    none = {**base, "eligible_profiles": [], "approved_profiles": []}
    assert script.decision_proposal(2, none, "retry_profile")["action"] == "human_review"


def test_profile_request_facts_on_the_real_effective_body(tmp_path, monkeypatch):
    cfg = tmp_path / 'runtime.json'
    cfg.write_text(json.dumps(script.runtime_config(18766, recovery='retry_profile')), encoding='utf-8')
    monkeypatch.setenv('ORCHESTRATOR_RUNTIME_CONFIG', str(cfg))
    from app.services.profiles_v1 import effective_body
    from app.services.prompt_library import APPROVED_PROFILE_PROMPTS
    suffix = APPROVED_PROFILE_PROMPTS[script.PROFILE["prompt_variant"]]
    body = {"model": "x", "stream": True, "messages": [{"role": "system", "content": "s"},
                                                        {"role": "user", "content": "do it"}]}
    effective = effective_body(body, script.PROFILE)
    facts = script.profile_request_facts(effective, suffix)
    assert facts == {"has_messages": True, "forbidden_keys": [], "model": "gpt-5.6-sol", "suffix_on_last_user": True}
    assert script.profile_request_facts(body, suffix)["suffix_on_last_user"] is False
    bad = script.profile_request_facts({**effective, "input": "x", "reasoning": {}}, suffix)
    assert bad["forbidden_keys"] == ["input", "reasoning"]


def _profile_snapshot():
    snap = _happy_snapshot()
    target = "n_excel_gpt_fw_script"
    snap["actions"]["recoveries"][0]["preconditions"] = {"action_type": "retry_profile"}
    snap["recovery_requests"] = [{"node": target, "path": "/v1/chat/completions", "host": "127.0.0.1",
                                  "has_messages": True, "forbidden_keys": [], "model": "gpt-5.6-sol",
                                  "suffix_on_last_user": True}]
    snap["events"] += [
        {"kind": "profile_effective_context", "node_key": target,
         "payload": {"profile_id": script.PROFILE_ID, "candidate": {"provider": "vibecode"}}},
        {"kind": "recovery_verification", "node_key": target, "payload": {"verified": True}}]
    return snap


def test_profile_snapshot_passes_and_each_profile_requirement_can_fail():
    run = lambda snap: script.verdict(script.evaluate(snap, break_node="script", break_mode="malformed",
                                                      recovery="retry_profile"))
    assert run(_profile_snapshot()) == (True, [])
    cases = {
        "decision_chose_retry_profile": lambda s: s["actions"]["recoveries"][0].update(preconditions={"action_type": "retry_same"}),
        "profile_effective_context_recorded": lambda s: s["events"].pop(-2),
        "profile_request_is_vibecode_chat_body": lambda s: s["recovery_requests"][0].update(forbidden_keys=["input"]),
        "profile_repair_suffix_on_last_user_message": lambda s: s["recovery_requests"][0].update(suffix_on_last_user=False),
        "profile_model_is_vibecode_model": lambda s: s["recovery_requests"][0].update(model="gpt-5-6-sol"),
        "recovery_verification_event_verified": lambda s: s["events"].pop(),
    }
    for name, mutate in cases.items():
        snap = _profile_snapshot()
        mutate(snap)
        ok, failed = run(snap)
        assert not ok and name in failed, (name, failed)
    snap = _profile_snapshot()
    snap["recovery_requests"][0]["host"] = "api.kie.ai"
    assert "profile_request_is_vibecode_chat_body" in run(snap)[1]


# ---------- composition is read from the group definition ----------

def test_composition_comes_from_the_group_definition():
    from app.services.node_groups import get_node_group
    group = get_node_group(script.GROUP_ID)
    assert script.node_suffixes() == tuple(n.local_key for n in group.nodes)
    assert script.node_keys() == tuple(n.preferred_id for n in group.nodes)
    assert all(k.startswith("n_excel_gpt_fw_") for k in script.node_keys())
    # the code-only report node never calls the model, so it cannot be broken
    assert "report" in script.node_suffixes() and "report" not in script.model_suffixes()
    assert set(script.model_suffixes()) <= set(script.node_suffixes())
    assert script.target_key("shots") == "n_excel_gpt_fw_shots"


def test_break_node_choices_follow_the_group_and_reject_unknown_nodes():
    for suffix in script.model_suffixes():
        assert script._args(["--work-dir", "w", "--break-node", suffix]).break_node == suffix
    for gone in ("check_script", "report", "nonexistent"):
        if gone not in script.model_suffixes():
            with pytest.raises(SystemExit):
                script._args(["--work-dir", "w", "--break-node", gone])


def test_action_answer_carries_the_legacy_main_action_only_on_request():
    plain = json.loads(script.valid_answer("action", [FRAME]))["ops"][0]["fields"]
    legacy = json.loads(script.valid_answer("action", [FRAME], with_action=True))["ops"][0]["fields"]
    assert plain == {"площадка": script.PLAN}
    assert legacy == {"главное_действие": script.ACTION, "площадка": script.PLAN}
