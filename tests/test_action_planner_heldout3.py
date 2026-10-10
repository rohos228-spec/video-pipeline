"""Отложенный набор №3 планировщика: валидность, изоляция от основного набора, наборов №1/№2 и правил.

Все проверки офлайн, без сети. Набор написан до просмотра ответов планировщика и не подгоняется под правила:
тесты проверяют только форму, эталоны по схеме каталога и то, что фразы не просочились в другие наборы и в код правил.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.services.action_layer.spec import ensure_builtin_actions, registry
from app.services.action_layer import schema as schema_mod

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
RULE_FILES = ("app/services/action_layer/planner_rules.py", "app/services/action_layer/planner_catalog.py",
              "app/services/action_layer/planner_guard.py", "app/services/action_layer/planner_context.py",
              "app/services/action_layer/planner_autofill.py", "app/services/action_layer/planner_uploads.py",
              "app/services/action_layer/planner.py")
_QUOTED = "[\"'«]([^\"'»\n]{6,})[\"'»]"
SHINGLE = 6  # слов подряд: совпадение такой длины с отложенной фразой считается просочившимся текстом


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


def _vars() -> dict:
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("eval_seed", ROOT / "scripts" / "eval_seed.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.expected_vars(2)


# --- валидность набора №3 -----------------------------------------------------------------------

def test_heldout3_is_big_and_diverse() -> None:
    assert len(HELD3) >= 100
    assert {c["outcome"] for c in HELD3} == {"plan", "clarify", "refuse"}
    ids = [c["id"] for c in HELD3]
    assert len(set(ids)) == len(ids) and all(i.startswith("h3_") for i in ids)
    texts = [_norm(c["text"]) for c in HELD3]
    assert len(set(texts)) == len(texts), "повтор фразы внутри набора"
    wfs = {c["workflow"] for c in HELD3}
    for need in ("queue", "project", "upload attached", "upload no file", "multistep", "ambiguous", "out_of_catalog",
                 "changes", "hitl", "media recovery", "node"):
        assert need in wfs, need
    assert sum(1 for c in HELD3 if "multistep" in c.get("tags", [])) >= 5
    assert sum(1 for c in HELD3 if c["outcome"] == "clarify") >= 10
    assert sum(1 for c in HELD3 if c["outcome"] == "refuse") >= 10
    ensure_builtin_actions()
    used = {s["action_id"] for c in HELD3 for s in c["steps"]}
    assert len(used) >= 60, len(used)


def test_heldout3_expectations_follow_the_catalog_schema() -> None:
    ensure_builtin_actions()
    variables = _vars()
    from scripts.eval_action_planner import resolve_vars
    for case in HELD3:
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
            loose = {k: v for k, v in spec.params_schema.items() if k != "required"}  # сервер добавит недостающее
            errors = schema_mod.validate(loose, {k: v for k, v in step["params"].items() if k != "source_path"})
            assert not errors, (case["id"], aid, errors)


def test_heldout3_clarify_and_attachment_cases_are_well_formed() -> None:
    for case in HELD3:
        for att in (case.get("context") or {}).get("attachments", []):
            assert {"kind", "name", "size"} <= set(att), case["id"]
        if case["outcome"] == "clarify":
            assert case["steps"] == [] and all(x == x.lower() for x in case.get("clarify_contains", [])), case["id"]
        if case["outcome"] == "refuse":
            assert case["steps"] == [], case["id"]


# --- изоляция: фразы не пересекаются с другими наборами и не попадают в правила -------------------

def test_no_text_is_shared_between_all_sets() -> None:
    sets = {"main": MAIN, "held1": HELD1, "held2": HELD2, "held3": HELD3}
    seen: dict[str, str] = {}
    for name, cases in sets.items():
        mine = {_norm(c["text"]) for c in cases}
        for key in mine:
            assert key not in seen, f"фраза {key!r} из {name} уже есть в {seen.get(key)}"
        seen.update({k: name for k in mine})
    for other in (HELD1, HELD2, MAIN):
        assert {c["id"] for c in HELD3}.isdisjoint({c["id"] for c in other})


def test_heldout3_phrases_do_not_leak_into_main_fixture_or_rules() -> None:
    held = {f"heldout3:{c['id']}": (_norm(c["text"]), _shingles(c["text"])) for c in HELD3}
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


def test_eval_script_knows_heldout3() -> None:
    import scripts.eval_action_planner as m
    assert "heldout3" in m.SETS
    cases = m.load_selected("heldout3")
    assert len(cases) == len(HELD3) and {c["set"] for c in cases} == {"heldout3"}
    assert "heldout3" not in {c["set"] for c in m.load_selected("both")}
    assert set(m.summarize_by_set(
        [{"id": "x", "outcome": "plan", "set": "heldout3", "ok": True}])) == {"heldout3"}
