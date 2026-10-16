from __future__ import annotations

import pytest
from sqlalchemy import text

from app.services.change_control import service, shadow
from app.services.change_control import store as cs
from app.services.change_control.errors import ChangeFailed, ChangeRefused, StaleBase
from tests.change_control_support import (
    GPT_PROMPT, HUMAN, PLAN_PROMPT, FakeGateway, cc, canvas_meta, prompts_root)

PLAN_TARGET = "project:1:n_plan:prompt"
SPLIT_TARGET = "project:1:n_split:step_params"
GPT_TARGET = "project:1:n_gpt:prompt"
OPT_TARGET = "project:1:n_gpt:options"


async def _propose(cc, **kw):
    base = dict(tier="T1", kind="step_params", project_id=1, node_key="n_split",
                rationale="тест", proposer=HUMAN,
                candidate={"step": "split", "values": {"cell_max_chars": 600}})
    base.update(kw)
    return await service.propose(cc.gw, **base)


async def _apply(cc, view, **kw):
    return await service.apply(cc.gw, view["id"], view["digest"], approved_by=kw.pop("by", HUMAN),
                               **kw)


async def _refused(coro, code):
    with pytest.raises(ChangeRefused) as exc:
        await coro
    assert exc.value.code == code, (exc.value.code, str(exc.value))
    return exc.value



async def test_t3_is_only_recorded_for_a_developer_and_never_applied(cc) -> None:
    view = await service.propose(
        cc.gw, tier="T3", kind="developer_review", rationale="нужна правка кода",
        proposer="planner:x", candidate={"summary": "сменить валидатор",
                                         "diff": "--- a/x.py\n+++ b/x.py\n@@\n-a\n+b", "path": "x.py"})
    assert view["status"] == "developer_review" and view["applicable"] is False
    assert view["can_apply"] is False
    await _refused(_apply(cc, view), "tier_not_applicable")
    assert cc.gw.patches == []
    async with cc.factory() as s:
        assert (await s.execute(text("select count(*) from config_versions"))).scalar() == 0
    events = [(h.event, h.outcome) for h in await cc.history()]
    assert ("change_proposed", "developer_review") in events
    assert ("change_applied", "refused") in events
    await _refused(service.propose(
        cc.gw, tier="T3", kind="developer_review", rationale="r", proposer=HUMAN,
        candidate={"summary": "s", "diff": "api_key=sk-abcdefghijkl123456"}), "lint_failed")


async def test_t0_is_advice_only(cc) -> None:
    view = await service.propose(cc.gw, tier="T0", kind="suggestion", rationale="r",
                                 proposer="planner:x", candidate={"text": "попробуйте иной промпт"})
    assert view["status"] == "proposed" and view["can_apply"] is False
    await _refused(_apply(cc, view), "tier_not_applicable")


async def test_kind_must_belong_to_tier_and_input_is_strict(cc) -> None:
    await _refused(_propose(cc, tier="T2"), "kind_tier_mismatch")
    await _refused(_propose(cc, tier="T9"), "bad_tier")
    await _refused(_propose(cc, rationale="  "), "bad_rationale")
    await _refused(_propose(cc, proposer=""), "proposer_required")
    await _refused(_propose(cc, project_id=None), "project_required")
    await _refused(_propose(cc, project_id=2), "project_not_found")
    await _refused(_propose(cc, candidate={"step": "split"}), "bad_candidate")



async def test_allow_list_refuses_everything_outside_the_list(cc) -> None:
    await _refused(_propose(cc, node_key="n_hero"), "node_type_not_allowed")
    await _refused(_propose(cc, node_key="n_missing"), "node_not_found")
    await _refused(_propose(cc, node_key="../x"), "bad_node_key")
    await _refused(_propose(cc, kind="node_options", node_key="n_plan",
                            candidate={"values": {"outputMode": "text"}}), "kind_not_allowed")
    await _refused(_propose(cc, kind="node_options", node_key="n_gpt",
                            candidate={"values": {"modelId": "gpt-x"}}), "field_not_allowed")
    await _refused(_propose(cc, kind="node_options", node_key="n_gpt",
                            candidate={"values": {"takeFromEdges": False}}), "field_not_allowed")
    await _refused(_propose(cc, candidate={"step": "split", "values": {"provider": 1}}),
                   "field_not_allowed")
    await _refused(_propose(cc, candidate={"step": "plan", "values": {"duration_seconds": 80}}),
                   "field_not_allowed")
    await _refused(_propose(cc, kind="prompt_variant", node_key="n_plan",
                            candidate={"variant": "no_such"}), "variant_not_found")


async def test_numeric_limits_caps_and_change_factor(cc) -> None:
    err = await _refused(_propose(cc, candidate={"step": "split", "values": {"cell_max_chars": 2500}}),
                         "lint_failed")
    assert any("outside" in e for e in err.errors)
    err = await _refused(_propose(cc, candidate={"step": "split", "values": {"cell_max_chars": 1000}}),
                         "lint_failed")
    assert any("exceeds x2" in e for e in err.errors)
    err = await _refused(_propose(cc, candidate={"step": "split", "values": {"cell_min_chars": 500}}),
                         "lint_failed")
    assert any("must not exceed" in e for e in err.errors)
    await _refused(_propose(cc, candidate={"step": "split", "values": {"cell_max_chars": True}}),
                   "lint_failed")
    await _refused(_propose(cc, candidate={"step": "split", "values": {"cell_max_chars": 400}}),
                   "lint_failed")
    err = await _refused(_propose(cc, candidate={"step": "split", "values": {"cell_avg_min": 150}}),
                         "lint_failed")
    assert any("no current value" in e for e in err.errors)


async def test_node_options_must_take_effect(cc) -> None:
    view = await _propose(cc, kind="node_options", node_key="n_gpt",
                          candidate={"values": {"outputMode": "text", "useSnapshot": True}})
    assert "outputMode" in view["diff"]
    await _refused(_propose(cc, kind="node_options", node_key="n_gpt",
                            candidate={"values": {"outputMode": "weird"}}), "lint_failed")
    await _refused(_propose(cc, kind="node_options", node_key="n_gpt",
                            candidate={"values": {"emitKinds": []}}), "lint_failed")
    await _refused(_propose(cc, kind="node_options", node_key="n_gpt",
                            candidate={"values": {"checkFix": "yes"}}), "lint_failed")
    cc.gw.state["meta"]["excel_gpt_nodes"] = {"n_gpt": {"useSnapshot": True}}
    await _refused(_propose(cc, kind="node_options", node_key="n_gpt",
                            candidate={"values": {"useSnapshot": True}}), "lint_failed")



async def test_step_params_apply_and_rollback_round_trip_with_version_rows(cc) -> None:
    view = await _propose(cc)
    assert view["status"] == "proposed" and view["base_version"] == 0 and view["can_apply"]
    assert view["diff"] == "split.cell_max_chars: 400 -> 600"
    res = await _apply(cc, view)
    assert res["status"] == "applied" and res["version"] == 2
    assert cc.gw.state["meta"]["node_step_params"]["split"] == {
        "cell_min_chars": 100, "cell_max_chars": 600}
    rows = await cc.versions(SPLIT_TARGET)
    assert [(r.version, r.parent_version, r.kind) for r in rows] == [
        (1, 0, "baseline"), (2, 1, "apply")]
    assert rows[0].content["values"]["cell_max_chars"] == 400
    assert rows[1].source_proposal_id == view["id"] and rows[1].applied_by == HUMAN
    row = await cc.proposal(view["id"])
    assert row.status == "applied" and row.applied_version == 2 and row.approved_by == HUMAN

    back = await service.rollback(cc.gw, SPLIT_TARGET, 2, approved_by=HUMAN, reason="не понравилось")
    assert back["version"] == 3 and back["restored_from_version"] == 1
    assert cc.gw.state["meta"]["node_step_params"]["split"]["cell_max_chars"] == 400
    rows = await cc.versions(SPLIT_TARGET)
    assert [(r.version, r.parent_version, r.kind) for r in rows] == [
        (1, 0, "baseline"), (2, 1, "apply"), (3, 2, "rollback")]
    assert (await cc.proposal(view["id"])).status == "rolled_back"
    listed = await service.list_versions(SPLIT_TARGET)
    assert listed["current_version"] == 3 and [v["version"] for v in listed["items"]] == [3, 2, 1]
    again = await service.rollback(cc.gw, SPLIT_TARGET, 3, approved_by=HUMAN)
    assert again["version"] == 4 and again["restored_from_version"] == 2
    assert cc.gw.state["meta"]["node_step_params"]["split"]["cell_max_chars"] == 600
    await _refused(service.rollback(cc.gw, SPLIT_TARGET, 1, approved_by=HUMAN), "stale_base")


async def test_version_history_is_append_only(cc) -> None:
    await _apply(cc, await _propose(cc))
    async with cc.factory() as s:
        for sql in ("update config_versions set content='{}'", "delete from config_versions"):
            with pytest.raises(Exception, match="append-only"):
                await s.execute(text(sql))
            await s.rollback()


async def test_prompt_variant_non_excel_uses_the_node_slot_not_the_global_override(cc) -> None:
    view = await _propose(cc, kind="prompt_variant", node_key="n_plan",
                          candidate={"variant": "plan_alt"})
    assert view["tier"] == "T1" and view["candidate"]["variant_sha256"]
    await _apply(cc, view)
    assert cc.gw.state["meta"]["prompt_slot_variants"] == {"n_plan": {"main": "plan_alt"}}
    from app.services import prompt_library as pl
    assert pl.resolve_project_prompt_with_source(
        {}, "plan", meta=cc.gw.state["meta"]) == ("plan_alt", "slot")
    await service.rollback(cc.gw, PLAN_TARGET, 2, approved_by=HUMAN)
    assert cc.gw.state["meta"]["prompt_slot_variants"]["n_plan"]["main"] == "default"
    assert cc.master_bytes("01_plan/default.md") == PLAN_PROMPT.encode()
    assert all("prompt_overrides" not in b for b in cc.gw.patches)
    assert cc.gw.state["prompt_overrides"] == {}


async def test_an_explicit_project_override_shadows_the_slot_and_is_refused(cc) -> None:
    cc.gw.state["prompt_overrides"] = {"plan": "plan_alt"}
    err = await _refused(_propose(cc, kind="prompt_variant", node_key="n_plan",
                                  candidate={"variant": "default"}), "lint_failed")
    assert any("precedence" in e for e in err.errors)
    await _refused(_propose(cc, tier="T2", kind="prompt_text", node_key="n_plan",
                            candidate={"text": NEW_PLAN}), "lint_failed")


async def test_prompt_variant_excel_node_uses_its_slot(cc) -> None:
    view = await _propose(cc, kind="prompt_variant", node_key="n_gpt",
                          candidate={"variant": "cc_test_alt"})
    await _apply(cc, view)
    assert cc.gw.state["meta"]["prompt_slot_variants"] == {"n_gpt": {"main": "cc_test_alt"}}
    await service.rollback(cc.gw, GPT_TARGET, 2, approved_by=HUMAN)
    assert cc.gw.state["meta"]["prompt_slot_variants"]["n_gpt"]["main"] == "default"


async def test_node_options_apply_and_rollback_restore_effective_values(cc) -> None:
    view = await _propose(cc, kind="node_options", node_key="n_gpt",
                          candidate={"values": {"outputMode": "text"}})
    await _apply(cc, view)
    assert cc.gw.state["meta"]["excel_gpt_nodes"]["n_gpt"] == {"outputMode": "text"}
    await service.rollback(cc.gw, OPT_TARGET, 2, approved_by=HUMAN)
    assert cc.gw.state["meta"]["excel_gpt_nodes"]["n_gpt"] == {"outputMode": "project_file"}


NEW_PLAN = PLAN_PROMPT.replace("Keep the structure", "Keep strictly the structure")


async def test_t2_text_creates_a_project_scoped_variant_and_never_edits_master(cc) -> None:
    view = await _propose(cc, tier="T2", kind="prompt_text", node_key="n_plan",
                          candidate={"text": NEW_PLAN})
    assert view["candidate"]["variant"].startswith("cc-n-plan-")
    assert "+Keep strictly" in view["diff"] or "Keep strictly" in view["diff"]
    res = await _apply(cc, view)
    name = view["candidate"]["variant"]
    assert cc.gw.state["meta"]["prompt_slot_variants"] == {"n_plan": {"main": name}}
    assert (cc.prompts / "01_plan" / f"{name}.md").read_text(encoding="utf-8") == NEW_PLAN
    assert cc.master_bytes("01_plan/default.md") == PLAN_PROMPT.encode()
    from app.services import prompt_library as pl
    assert pl.resolve_project_prompt_with_source(
        {}, "plan", meta=cc.gw.state["meta"]) == (name, "slot")
    rows = await cc.versions(PLAN_TARGET)
    assert rows[1].content["text"] == NEW_PLAN and rows[0].content["variant"] == "default"
    await service.rollback(cc.gw, PLAN_TARGET, res["version"], approved_by=HUMAN)
    assert cc.gw.state["meta"]["prompt_slot_variants"]["n_plan"]["main"] == "default"
    assert (cc.prompts / "01_plan" / f"{name}.md").exists()


async def test_t2_text_on_an_excel_node_writes_a_new_agent_file_and_slot(cc) -> None:
    text_ = GPT_PROMPT.replace("for the style line", "for the style line, briefly")
    view = await _propose(cc, tier="T2", kind="prompt_text", node_key="n_gpt",
                          candidate={"text": text_})
    await _apply(cc, view)
    name = view["candidate"]["variant"]
    assert cc.gw.state["meta"]["prompt_slot_variants"]["n_gpt"]["main"] == name
    assert (cc.prompts / "05_excel_gpt" / f"{name}.md").read_text(encoding="utf-8") == text_
    assert cc.master_bytes("05_excel_gpt/default.md") == GPT_PROMPT.encode()



@pytest.mark.parametrize("node,base,mutate,needle", [
    ("n_plan", PLAN_PROMPT, lambda t: t.replace("{{VAR:PROJECT_TOPIC}}", "tema"), "placeholders lost"),
    ("n_plan", PLAN_PROMPT, lambda t: t + " {{VAR:NEW_ONE}}", "unknown placeholders"),
    ("n_plan", PLAN_PROMPT, lambda t: "short", "shorter than"),
    ("n_plan", PLAN_PROMPT, lambda t: t * 6, "of the current size (max"),
    ("n_plan", PLAN_PROMPT, lambda t: t + "\x00 nul", "control characters"),
    ("n_plan", PLAN_PROMPT, lambda t: t, "equals the current prompt"),
    ("n_plan", PLAN_PROMPT, lambda t: t + " token=sk-abcdefghijk1234", "credential-like"),
    ("n_gpt", GPT_PROMPT, lambda t: t.replace("apply-ops", "free form"), "contract markers lost"),
    ("n_gpt", GPT_PROMPT, lambda t: t.replace("frame_uuid", "id"), "contract markers lost"),
])
async def test_prompt_text_lints_refuse(cc, node, base, mutate, needle) -> None:
    err = await _refused(_propose(cc, tier="T2", kind="prompt_text", node_key=node,
                                  candidate={"text": mutate(base)}), "lint_failed")
    assert any(needle in e for e in err.errors), err.errors
    assert cc.gw.patches == [] and not list((cc.prompts / "01_plan").glob("cc-*"))


async def test_prompt_text_refused_when_the_project_composes_prompts_from_blocks(cc) -> None:
    cc.gw.state["prompt_overrides"] = {"use_blocks_v2": True}
    err = await _refused(_propose(cc, tier="T2", kind="prompt_text", node_key="n_plan",
                                  candidate={"text": NEW_PLAN}), "lint_failed")
    assert any("blocks" in e for e in err.errors)


async def test_lint_failure_at_apply_is_recorded_as_failed_and_nothing_is_written(cc) -> None:
    view = await _propose(cc, tier="T2", kind="prompt_text", node_key="n_plan",
                          candidate={"text": NEW_PLAN})
    bad = dict(view["candidate"], text=NEW_PLAN + " password=hunter2hunter2")
    bad["variant"] = service.targets.PromptHandler.variant_name_for("n_plan", bad["text"])
    async with cc.factory() as s:
        row = await s.get(cs.ChangeProposal, view["id"])
        row.candidate = bad
        row.digest = service._proposal_digest({
            "tier": row.tier, "project_id": row.project_id, "node_key": row.node_key,
            "kind": row.kind, "target": row.target, "base_version": row.base_version,
            "candidate": bad})
        digest = row.digest
        await s.commit()
    with pytest.raises(ChangeRefused) as exc:
        await service.apply(cc.gw, view["id"], digest, approved_by=HUMAN)
    assert exc.value.code == "lint_failed"
    assert (await cc.proposal(view["id"])).status == "failed"
    assert cc.gw.patches == [] and await cc.versions(PLAN_TARGET) == []
    assert not list((cc.prompts / "01_plan").glob("cc-*"))



async def test_stale_base_when_another_change_was_applied_first(cc) -> None:
    first = await _propose(cc)
    second = await _propose(cc, candidate={"step": "split", "values": {"cell_max_chars": 500}})
    await _apply(cc, first)
    err = await _refused(_apply(cc, second), "stale_base")
    assert err.http == 409
    assert (await cc.proposal(second["id"])).status == "proposed"
    assert cc.gw.state["meta"]["node_step_params"]["split"]["cell_max_chars"] == 600
    assert ("change_applied", "stale_base") in [(h.event, h.outcome) for h in await cc.history()]


async def test_stale_base_when_the_live_value_was_edited_by_hand(cc) -> None:
    view = await _propose(cc)
    cc.gw.state["meta"]["node_step_params"]["split"]["cell_max_chars"] = 450
    await _refused(_apply(cc, view), "stale_base")
    assert cc.gw.patches == [] and await cc.versions(SPLIT_TARGET) == []


async def test_cas_pointer_refuses_a_concurrent_second_writer(cc) -> None:
    await _apply(cc, await _propose(cc))
    with pytest.raises(StaleBase):
        await service._commit_versions([dict(
            target=SPLIT_TARGET, base=1, kind="apply", content={"values": {}}, project_id=1,
            node_key="n_split", proposal_id=None, by=HUMAN)])
    assert [r.version for r in await cc.versions(SPLIT_TARGET)] == [1, 2]


async def test_rollback_needs_the_current_version(cc) -> None:
    await _apply(cc, await _propose(cc))
    await _refused(service.rollback(cc.gw, SPLIT_TARGET, 5, approved_by=HUMAN), "stale_base")
    await _refused(service.rollback(cc.gw, "project:1:n_plan:prompt", 2, approved_by=HUMAN),
                   "stale_base")
    await _refused(service.rollback(cc.gw, "bad target", 2, approved_by=HUMAN), "bad_target")


async def test_digest_must_match_and_a_tampered_row_is_refused(cc) -> None:
    view = await _propose(cc)
    await _refused(service.apply(cc.gw, view["id"], "0" * 64, approved_by=HUMAN), "digest_mismatch")
    async with cc.factory() as s:
        row = await s.get(cs.ChangeProposal, view["id"])
        row.candidate = {"step": "split", "values": {"cell_max_chars": 700}}
        await s.commit()
    await _refused(_apply(cc, view), "proposal_tampered")
    assert cc.gw.patches == []
    await _refused(service.apply(cc.gw, "f" * 32, view["digest"], approved_by=HUMAN),
                   "proposal_not_found")


async def test_applied_proposal_cannot_be_applied_twice_and_duplicates_are_merged(cc) -> None:
    view = await _propose(cc)
    assert (await _propose(cc))["id"] == view["id"]
    await _apply(cc, view)
    await _refused(_apply(cc, view), "status_not_applicable")
    assert len(await cc.versions(SPLIT_TARGET)) == 2



async def test_failed_verification_restores_the_old_value_and_records_it(cc) -> None:
    view = await _propose(cc)
    cc.gw.corrupt_after_patch = lambda st: st["meta"]["node_step_params"]["split"].update(
        cell_max_chars=999)
    with pytest.raises(ChangeFailed) as exc:
        await _apply(cc, view)
    assert exc.value.code == "verification_failed"
    assert cc.gw.state["meta"]["node_step_params"]["split"]["cell_max_chars"] == 400
    rows = await cc.versions(SPLIT_TARGET)
    assert [(r.version, r.kind) for r in rows] == [(1, "baseline"), (2, "apply"), (3, "auto_rollback")]
    assert rows[2].content == rows[0].content
    row = await cc.proposal(view["id"])
    assert row.status == "failed" and "auto-rolled back" in row.error
    assert ("change_applied", "auto_rolled_back") in [(h.event, h.outcome) for h in await cc.history()]


async def test_a_failing_write_is_compensated_too(cc) -> None:
    view = await _propose(cc, tier="T2", kind="prompt_text", node_key="n_plan",
                          candidate={"text": NEW_PLAN})
    cc.gw.fail_patches = 1
    with pytest.raises(ChangeFailed) as exc:
        await _apply(cc, view)
    assert exc.value.code == "verification_failed" and "write failed" in str(exc.value)
    assert "prompt_slot_variants" not in cc.gw.state["meta"]
    assert (await cc.proposal(view["id"])).status == "failed"
    assert [r.kind for r in await cc.versions(PLAN_TARGET)] == ["baseline", "apply", "auto_rollback"]


async def test_when_restoring_fails_too_the_proposal_is_marked_for_manual_review(cc) -> None:
    view = await _propose(cc)

    def break_it(st):
        st["meta"]["node_step_params"]["split"]["cell_max_chars"] = 999
        cc.gw.fail_patches = 1

    cc.gw.corrupt_after_patch = break_it
    with pytest.raises(ChangeFailed) as exc:
        await _apply(cc, view)
    assert exc.value.code == "rollback_failed"
    assert "manual review" in (await cc.proposal(view["id"])).error


async def test_rollback_verification_failure_restores_the_applied_value(cc) -> None:
    await _apply(cc, await _propose(cc))
    cc.gw.corrupt_after_patch = lambda st: st["meta"]["node_step_params"]["split"].update(
        cell_max_chars=123)
    with pytest.raises(ChangeFailed):
        await service.rollback(cc.gw, SPLIT_TARGET, 2, approved_by=HUMAN)
    assert cc.gw.state["meta"]["node_step_params"]["split"]["cell_max_chars"] == 600
    assert [r.kind for r in await cc.versions(SPLIT_TARGET)][-2:] == ["rollback", "auto_rollback"]



@pytest.mark.parametrize("who", ["planner:x", "model:y", "agent:z", "system", ""])
async def test_only_a_human_can_apply_or_roll_back(cc, who) -> None:
    view = await service.propose(
        cc.gw, tier="T1", kind="step_params", project_id=1, node_key="n_split", proposer="planner:p",
        rationale="r", candidate={"step": "split", "values": {"cell_max_chars": 600}})
    assert view["proposer"] == "planner:p"
    await _refused(service.apply(cc.gw, view["id"], view["digest"], approved_by=who),
                   "operator_required")
    await _refused(service.rollback(cc.gw, SPLIT_TARGET, 2, approved_by=who), "operator_required")
    assert cc.gw.patches == []


async def test_running_project_is_not_changed(cc) -> None:
    view = await _propose(cc)
    cc.gw.state["status"] = "splitting"
    await _refused(_apply(cc, view), "project_busy")
    cc.gw.state["status"] = "new"
    await _apply(cc, view)
    cc.gw.state["status"] = "splitting"
    await _refused(service.rollback(cc.gw, SPLIT_TARGET, 2, approved_by=HUMAN), "project_busy")


async def test_shadow_hooks_default_to_none_and_can_veto(cc) -> None:
    assert shadow.registered() == []
    seen = []
    shadow.register_shadow_hook("probe", lambda p: seen.append(p["id"]) or {"ok": True, "note": "fine"})
    try:
        view = await _propose(cc)
        await _apply(cc, view)
        assert seen == [view["id"]]
        assert (await cc.proposal(view["id"])).shadow == [{"name": "probe", "ok": True, "note": "fine"}]
        shadow.register_shadow_hook("veto", lambda p: {"ok": False, "note": "metric fell"})
        second = await _propose(cc, candidate={"step": "split", "values": {"cell_max_chars": 700}})
        await _refused(_apply(cc, second), "shadow_failed")
        assert (await cc.proposal(second["id"])).status == "failed"
        assert cc.gw.state["meta"]["node_step_params"]["split"]["cell_max_chars"] == 600
    finally:
        shadow.unregister_shadow_hook("probe")
        shadow.unregister_shadow_hook("veto")


async def test_audit_rows_for_propose_apply_and_rollback(cc) -> None:
    view = await _propose(cc)
    await _apply(cc, view, plan_id="plan-1")
    await service.rollback(cc.gw, SPLIT_TARGET, 2, approved_by=HUMAN, plan_id="plan-1")
    rows = [h for h in await cc.history() if h.event.startswith("change_")]
    assert [(h.event, h.outcome) for h in rows] == [
        ("change_proposed", "proposed"), ("change_applied", "applied"),
        ("change_rolled_back", "rolled_back")]
    assert rows[0].actor == HUMAN and rows[1].plan_id == "plan-1"
    assert rows[1].params["proposal_id"] == view["id"] and rows[1].params["version"] == 2
    assert rows[2].params["target"] == SPLIT_TARGET
    assert all("text" not in (h.params or {}) for h in rows)
