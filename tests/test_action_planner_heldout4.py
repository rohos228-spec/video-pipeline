from __future__ import annotations

import json
import re
from pathlib import Path

from app.services.action_layer import planner
from app.services.action_layer import schema as schema_mod
from app.services.action_layer.spec import ensure_builtin_actions, registry
from tests.action_layer_support import env
from tests.test_action_layer_planner import res
from tests.test_action_planner_oracle import OP, PID, Const, oracle_env, oracle_reply
from tests.test_action_planner_eval_script import script
from tests.test_action_planner_seed_context import load_seed

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
RULE_FILES = ("app/services/action_layer/planner_rules.py", "app/services/action_layer/planner_catalog.py",
              "app/services/action_layer/planner_guard.py", "app/services/action_layer/planner_context.py",
              "app/services/action_layer/planner_autofill.py", "app/services/action_layer/planner_uploads.py",
              "app/services/action_layer/planner.py")
_QUOTED = "[\"'«]([^\"'»\n]{6,})[\"'»]"
SHINGLE = 6


def _cases(name: str) -> list[dict]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))["cases"]


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-zа-я0-9_]+", str(text).lower().replace("ё", "е")))


def _shingles(text: str, n: int = SHINGLE) -> set[str]:
    w = _norm(text).split()
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


MAIN = _cases("action_planner_eval.json")
HELD1 = _cases("action_planner_heldout.json")
HELD2 = _cases("action_planner_heldout2.json")
HELD3 = _cases("action_planner_heldout3.json")
HELD4 = _cases("action_planner_heldout4.json")


def _vars() -> dict:
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("eval_seed", ROOT / "scripts" / "eval_seed.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.expected_vars(2)



def test_heldout4_is_big_and_diverse() -> None:
    assert len(HELD4) >= 120
    assert {c["outcome"] for c in HELD4} == {"plan", "clarify", "refuse"}
    ids = [c["id"] for c in HELD4]
    assert len(set(ids)) == len(ids) and all(i.startswith("h4_") for i in ids)
    texts = [_norm(c["text"]) for c in HELD4]
    assert len(set(texts)) == len(texts), "повтор фразы внутри набора"
    wfs = {c["workflow"] for c in HELD4}
    for need in ("queue", "project", "upload attached", "upload no file", "multistep", "ambiguous", "out_of_catalog",
                 "changes", "hitl", "media recovery", "node"):
        assert need in wfs, need
    assert sum(1 for c in HELD4 if "multistep" in c.get("tags", [])) >= 10
    assert sum(1 for c in HELD4 if c["outcome"] == "clarify") >= 15
    assert sum(1 for c in HELD4 if c["outcome"] == "refuse") >= 15
    ensure_builtin_actions()
    used = {s["action_id"] for c in HELD4 for s in c["steps"]}
    assert len(used) >= 60, len(used)


def test_heldout4_style_mix() -> None:
    plans = [c for c in HELD4 if c["outcome"] == "plan"]
    words = [len(_norm(c["text"]).split()) for c in plans]
    assert sum(1 for n in words if n <= 2) >= 12, "мало коротких команд"
    assert sum(1 for n in words if n >= 14) >= 8, "мало длинных разговорных просьб"
    assert sum(1 for c in plans if len(c["steps"]) >= 3) >= 1


def test_heldout4_expectations_follow_the_catalog_schema() -> None:
    ensure_builtin_actions()
    variables = _vars()
    from scripts.eval_action_planner import resolve_vars
    for case in HELD4:
        if case["outcome"] != "plan":
            assert case["steps"] == [], case["id"]
            continue
        assert case["steps"], case["id"]
        for step in resolve_vars(case["steps"], variables):
            aid = step["action_id"]
            spec = registry.find(aid)
            assert spec is not None, (case["id"], aid)
            props = (spec.params_schema or {}).get("properties", {})
            unknown = set(step["params"]) - set(props)
            assert not unknown, (case["id"], aid, sorted(unknown))
            loose = {k: v for k, v in spec.params_schema.items() if k != "required"}
            errors = schema_mod.validate(loose, {k: v for k, v in step["params"].items() if k != "source_path"})
            assert not errors, (case["id"], aid, errors)


def test_heldout4_clarify_and_attachment_cases_are_well_formed() -> None:
    for case in HELD4:
        for att in (case.get("context") or {}).get("attachments", []):
            assert {"kind", "name", "size"} <= set(att), case["id"]
        if case["outcome"] == "clarify":
            assert case["steps"] == [] and all(x == x.lower() for x in case.get("clarify_contains", [])), case["id"]
        if case["outcome"] == "refuse":
            assert case["steps"] == [], case["id"]



def test_no_text_is_shared_between_all_sets() -> None:
    sets = {"main": MAIN, "held1": HELD1, "held2": HELD2, "held3": HELD3, "held4": HELD4}
    seen: dict[str, str] = {}
    for name, cases in sets.items():
        mine = {_norm(c["text"]) for c in cases}
        for key in mine:
            assert key not in seen, f"фраза {key!r} из {name} уже есть в {seen.get(key)}"
        seen.update({k: name for k in mine})
    for other in (HELD1, HELD2, HELD3, MAIN):
        assert {c["id"] for c in HELD4}.isdisjoint({c["id"] for c in other})


def test_heldout4_phrases_do_not_leak_into_main_fixture_or_rules() -> None:
    held = {f"heldout4:{c['id']}": (_norm(c["text"]), _shingles(c["text"])) for c in HELD4}
    haystacks = {"main-fixture": [c["text"] for c in MAIN]}
    for rel in RULE_FILES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        haystacks[rel] = re.findall(_QUOTED, src) + [src]
    leaks: list[str] = []
    for where, chunks in haystacks.items():
        padded = [" " + _norm(ch) + " " for ch in chunks]
        grams: set[str] = set()
        for chunk in chunks:
            grams |= _shingles(chunk)
        for key, (text, sh) in held.items():
            if len(text.split()) >= 3 and any(" " + text + " " in ch for ch in padded):
                leaks.append(f"{key} целиком в {where}")
            elif sh and sh & grams:
                leaks.append(f"{key} ({sorted(sh & grams)[0]}) в {where}")
    assert not leaks, leaks[:10]


def test_eval_script_knows_heldout4() -> None:
    import scripts.eval_action_planner as m
    assert "heldout4" in m.SETS
    cases = m.load_selected("heldout4")
    assert len(cases) == len(HELD4) and {c["set"] for c in cases} == {"heldout4"}
    assert "heldout4" not in {c["set"] for c in m.load_selected("both")}
    assert set(m.summarize_by_set(
        [{"id": "x", "outcome": "plan", "set": "heldout4", "ok": True}])) == {"heldout4"}



async def test_every_heldout4_case_passes_the_server_with_a_perfect_model(oracle_env) -> None:
    m = script()
    variables = load_seed().expected_vars(PID)
    failed: list[str] = []
    for case in HELD4:
        ctx = case.get("context") or {}
        planner.set_transport(Const(res(oracle_reply(case, variables))))
        steps, clar, code, expl = [], None, None, None
        try:
            out = await planner.plan_from_text(
                case["text"], operator=OP, project_id=PID, selected_scene=ctx.get("selected_scene"),
                selected_frame=ctx.get("selected_frame"),
                attachments=load_seed().fake_attachments(PID, ctx.get("attachments")))
            steps, clar, expl = out.reply.steps, out.reply.needs_clarification, out.reply.explanation_ru
        except planner.PlannerError as exc:
            code = f"{exc.code}: {exc}"
        row = m.score_case(case, steps, clar, code, expl, variables)
        if case["outcome"] == "refuse" and code:
            row["ok"] = True
        if not row["ok"]:
            failed.append(f"{case['id']} {case['text']!r}: {json.dumps(row, ensure_ascii=False, default=str)[:260]}")
    assert not failed, "\n".join(failed)
