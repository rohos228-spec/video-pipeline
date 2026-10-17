from __future__ import annotations

import json
import re
from pathlib import Path

from app.services.action_layer import schema as schema_mod
from app.services.action_layer.spec import ensure_builtin_actions, registry

ROOT = Path(__file__).resolve().parent.parent
FIX = ROOT / "tests" / "fixtures"
RULE_FILES = ("app/services/action_layer/planner_rules.py", "app/services/action_layer/planner_catalog.py",
              "app/services/action_layer/planner_guard.py", "app/services/action_layer/planner_context.py",
              "app/services/action_layer/planner_autofill.py", "app/services/action_layer/planner_uploads.py",
              "app/services/action_layer/planner.py")
_QUOTED = "[\"'«]([^\"'»\n]{6,})[\"'»]"
SHINGLE = 6
OTHER_SETS = ("action_planner_eval.json", "action_planner_heldout.json", "action_planner_heldout2.json",
              "action_planner_heldout3.json", "action_planner_heldout4.json", "action_planner_heldout5.json",
              "action_planner_heldout6.json", "action_planner_heldout7.json")


def _cases(name: str) -> list[dict]:
    return json.loads((FIX / name).read_text(encoding="utf-8"))["cases"]


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-zа-я0-9_]+", str(text).lower().replace("ё", "е")))


def _shingles(text: str, n: int = SHINGLE) -> set[str]:
    w = _norm(text).split()
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


HELD8 = _cases("action_planner_heldout8.json")


def _vars() -> dict:
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("eval_seed", ROOT / "scripts" / "eval_seed.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.expected_vars(2)


def test_heldout8_shape_ids_and_unique_texts() -> None:
    assert len(HELD8) >= 150
    assert {c["outcome"] for c in HELD8} == {"plan", "clarify", "refuse"}
    ids = [c["id"] for c in HELD8]
    assert len(set(ids)) == len(ids) and all(i.startswith("h8_") for i in ids)
    texts = [_norm(c["text"]) for c in HELD8]
    assert len(set(texts)) == len(texts), "повтор фразы внутри набора"
    for c in HELD8:
        assert {"text", "workflow", "context", "outcome", "steps", "id"} <= set(c), c.get("id")
        assert isinstance(c["text"], str) and c["text"].strip(), c["id"]
        assert isinstance(c["steps"], list) and isinstance(c["context"], dict), c["id"]
    assert sum(1 for c in HELD8 if c["outcome"] == "clarify") >= 15
    assert sum(1 for c in HELD8 if c["outcome"] == "refuse") >= 15
    assert sum(1 for c in HELD8 if "multistep" in c.get("tags", [])) >= 10
    assert sum(1 for c in HELD8 if c.get("context", {}).get("attachments")) >= 8


def test_heldout8_every_expected_action_exists_in_the_catalog() -> None:
    ensure_builtin_actions()
    variables = _vars()
    from scripts.eval_action_planner import resolve_vars
    used: set[str] = set()
    for case in HELD8:
        if case["outcome"] != "plan":
            assert case["steps"] == [], case["id"]
            continue
        assert case["steps"], case["id"]
        for step in resolve_vars(case["steps"], variables):
            aid = step["action_id"]
            used.add(aid)
            spec = registry.find(aid)
            assert spec is not None, (case["id"], aid)
            props = (spec.params_schema or {}).get("properties", {})
            unknown = set(step["params"]) - set(props)
            assert not unknown, (case["id"], aid, sorted(unknown))
            loose = {k: v for k, v in spec.params_schema.items() if k != "required"}
            errors = schema_mod.validate(loose, {k: v for k, v in step["params"].items() if k != "source_path"})
            assert not errors, (case["id"], aid, errors)
    assert len(used) >= 60, len(used)


def test_heldout8_clarify_refuse_and_attachment_cases_are_well_formed() -> None:
    for case in HELD8:
        for att in (case.get("context") or {}).get("attachments", []):
            assert {"kind", "name", "size"} <= set(att), case["id"]
        if case["outcome"] == "clarify":
            assert case["steps"] == [] and all(x == x.lower() for x in case.get("clarify_contains", [])), case["id"]
        if case["outcome"] == "refuse":
            assert case["steps"] == [], case["id"]
        if case["outcome"] == "plan":
            for step in case["steps"]:
                assert isinstance(step.get("params"), dict), case["id"]


def test_heldout8_has_no_text_shared_with_other_sets() -> None:
    seen: dict[str, str] = {}
    for name in OTHER_SETS:
        for case in _cases(name):
            seen.setdefault(_norm(case["text"]), name)
    grams: set[str] = set()
    for case in HELD8:
        key = _norm(case["text"])
        assert key not in seen, f"фраза {key!r} уже есть в {seen[key]}"
        grams |= _shingles(case["text"])
    ids_other = {c["id"] for name in OTHER_SETS for c in _cases(name)}
    assert {c["id"] for c in HELD8}.isdisjoint(ids_other)
    for name in OTHER_SETS:
        other = set()
        for case in _cases(name):
            other |= _shingles(case["text"])
        assert not (grams & other), (name, sorted(grams & other)[:3])


def test_heldout8_phrases_do_not_leak_into_rules() -> None:
    held = {c["id"]: (_norm(c["text"]), _shingles(c["text"])) for c in HELD8}
    leaks: list[str] = []
    for rel in RULE_FILES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        chunks = re.findall(_QUOTED, src) + [src]
        padded = [" " + _norm(ch) + " " for ch in chunks]
        grams: set[str] = set()
        for chunk in chunks:
            grams |= _shingles(chunk)
        for key, (text, sh) in held.items():
            if len(text.split()) >= 3 and any(" " + text + " " in ch for ch in padded):
                leaks.append(f"{key} целиком в {rel}")
            elif sh and sh & grams:
                leaks.append(f"{key} ({sorted(sh & grams)[0]}) в {rel}")
    assert not leaks, leaks[:10]


def test_eval_script_knows_heldout8() -> None:
    import scripts.eval_action_planner as m
    assert "heldout8" in m.SETS
    cases = m.load_selected("heldout8")
    assert len(cases) == len(HELD8) and {c["set"] for c in cases} == {"heldout8"}
    assert "heldout8" not in {c["set"] for c in m.load_selected("both")}
    assert set(m.summarize_by_set(
        [{"id": "x", "outcome": "plan", "set": "heldout8", "ok": True}])) == {"heldout8"}


def test_heldout8_expectations_fit_the_eval_seed() -> None:
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("eval_seed", ROOT / "scripts" / "eval_seed.py")
    seed = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = seed
    spec.loader.exec_module(seed)
    heads = {n for n, role in enumerate(seed.scene_layout(), 1) if role == "p"}

    def shots(node):
        if isinstance(node, dict):
            if node.get("shot") == 2:
                frame = node.get("frame_number")
                yield frame
            for v in node.values():
                yield from shots(v)
        elif isinstance(node, list):
            for v in node:
                yield from shots(v)

    for case in HELD8:
        if case["outcome"] != "plan":
            continue
        for step in case["steps"]:
            params = step["params"]
            for frame in shots(params):
                assert frame in seed.SHOT2_FRAMES, (case["id"], frame)
            if step["action_id"] == "publish.send":
                assert "caption" not in params, case["id"]
            for key in ("frame_number", "a_frame", "b_frame", "from_frame", "to_frame"):
                if key in params:
                    assert 1 <= params[key] <= seed.N_FRAMES, (case["id"], key)
            for key in ("frame_id", "after_frame_id", "left_frame_id", "right_frame_id"):
                if isinstance(params.get(key), str):
                    n = int(params[key].rsplit(".", 1)[1])
                    assert 1 <= n <= seed.N_FRAMES, (case["id"], key)
                    if step["action_id"] in ("montage.merge_scenes", "montage.explode_scene",
                                             "montage.improve_scene", "montage.generate_scene"):
                        assert n in heads, (case["id"], "ожидается голова сцены", n)
