"""Отложенный набор №2 планировщика: валидность, изоляция от основного набора и правил, диагностика транспорта.

Все проверки офлайн, без сети. Набор ни разу не прогонялся на правилах: тесты проверяют только форму,
эталоны по схеме каталога и то, что фразы отложенных наборов не просочились в основной набор и в код правил.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.services.action_layer import planner
from app.services.action_layer.spec import ensure_builtin_actions, registry
from tests.action_layer_support import env  # noqa: F401
from tests.test_action_layer_planner import OP, history_rows, res, use
from tests.test_action_planner_flow import PID, live, st  # noqa: F401

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


def _vars() -> dict:
    import importlib.util
    spec = importlib.util.spec_from_file_location("eval_seed", ROOT / "scripts" / "eval_seed.py")
    mod = importlib.util.module_from_spec(spec)
    import sys
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod.expected_vars(2)


# --- валидность набора №2 -----------------------------------------------------------------------

def test_heldout2_is_big_and_diverse() -> None:
    assert len(HELD2) >= 100
    assert {c["outcome"] for c in HELD2} == {"plan", "clarify", "refuse"}
    ids = [c["id"] for c in HELD2]
    assert len(set(ids)) == len(ids) and all(i.startswith("h2_") for i in ids)
    texts = [_norm(c["text"]) for c in HELD2]
    assert len(set(texts)) == len(texts), "повтор фразы внутри набора"
    wfs = {c["workflow"] for c in HELD2}
    for need in ("queue", "project", "upload attached", "upload no file", "multistep", "ambiguous", "out_of_catalog",
                 "changes", "hitl", "media recovery", "node"):
        assert need in wfs, need
    assert sum(1 for c in HELD2 if "multistep" in c.get("tags", [])) >= 5
    ensure_builtin_actions()
    used = {s["action_id"] for c in HELD2 for s in c["steps"]}
    assert len(used) >= 60, len(used)


def test_heldout2_expectations_follow_the_catalog_schema() -> None:
    ensure_builtin_actions()
    variables = _vars()
    from scripts.eval_action_planner import resolve_vars
    from app.services.action_layer import schema as schema_mod
    for case in HELD2:
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


def test_heldout2_clarify_and_attachment_cases_are_well_formed() -> None:
    for case in HELD2:
        for att in (case.get("context") or {}).get("attachments", []):
            assert {"kind", "name", "size"} <= set(att), case["id"]
        if case["outcome"] == "clarify":
            assert case["steps"] == [] and all(x == x.lower() for x in case.get("clarify_contains", [])), case["id"]


# --- изоляция: отложенные фразы не попадают в основной набор и в правила --------------------------

def test_no_text_is_shared_between_main_heldout1_and_heldout2() -> None:
    sets = {"main": MAIN, "held1": HELD1, "held2": HELD2}
    seen: dict[str, str] = {}
    for name, cases in sets.items():
        mine = {_norm(c["text"]) for c in cases}
        for key in mine:
            assert key not in seen, f"фраза {key!r} из {name} уже есть в {seen.get(key)}"
        seen.update({k: name for k in mine})
    assert {c["id"] for c in HELD1}.isdisjoint({c["id"] for c in HELD2})
    assert {c["id"] for c in MAIN}.isdisjoint({c["id"] for c in HELD2})


def test_heldout_phrases_do_not_leak_into_main_fixture_or_rules() -> None:
    held = {}
    for label, cases in (("heldout1", HELD1), ("heldout2", HELD2)):
        for c in cases:
            held[f"{label}:{c['id']}"] = (_norm(c["text"]), _shingles(c["text"]))
    haystacks = {"main-fixture": [c["text"] for c in MAIN]}
    for rel in RULE_FILES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        haystacks[rel] = re.findall(_QUOTED, src) + [src]
    leaks: list[str] = []
    for where, chunks in haystacks.items():
        padded = [" " + _norm(ch) + " " for ch in chunks]  # по границам слов и внутри одной строки
        grams = set()
        for chunk in chunks:
            grams |= _shingles(chunk)
        for key, (text, sh) in held.items():
            if len(text.split()) >= 3 and any(" " + text + " " in ch for ch in padded):
                leaks.append(f"{key} целиком в {where}")
            elif sh and sh & grams:
                leaks.append(f"{key} ({sorted(sh & grams)[0]}) в {where}")
    assert not leaks, leaks[:10]


def test_leak_check_detects_a_copied_phrase() -> None:
    phrase = HELD1[0]["text"] + " " + HELD1[1]["text"]
    assert _shingles(HELD1[1]["text"]) or len(_norm(HELD1[1]["text"]).split()) < SHINGLE
    long_one = next(c["text"] for c in HELD1 + HELD2 if len(_norm(c["text"]).split()) >= SHINGLE)
    assert _shingles(long_one) & _shingles("prefix " + long_one + " suffix")
    assert phrase


def test_eval_script_knows_heldout2() -> None:
    import scripts.eval_action_planner as m
    assert "heldout2" in m.SETS
    cases = m.load_selected("heldout2")
    assert len(cases) == len(HELD2) and {c["set"] for c in cases} == {"heldout2"}
    both = m.load_selected("both")
    assert {c["set"] for c in both} == {"main", "heldout", "heldout2"}
    assert set(m.summarize_by_set(
        [{"id": "x", "outcome": "plan", "set": "heldout2", "ok": True}])) == {"heldout2"}


# --- диагностика transport_error ----------------------------------------------------------------

def test_describe_exception_has_type_text_and_hides_secrets() -> None:
    assert planner.describe_exception(TimeoutError()).startswith("TimeoutError: timeout")
    out = planner.describe_exception(ValueError("bad key sk-abcdefghijklmnop12345 in reply"))
    assert out.startswith("ValueError:") and "sk-abcdefghijklmnop12345" not in out
    from app.services.gpt_api import GptApiError
    err = GptApiError("GPT HTTP 503", context={"status_code": 503, "retryable": True, "body": "x" * 5000})
    text = planner.describe_exception(err)
    assert "GptApiError: GPT HTTP 503" in text and "status_code=503" in text and len(text) <= 300
    try:
        try:
            raise OSError("connection reset")
        except OSError as inner:
            raise RuntimeError("wrapped") from inner
    except RuntimeError as outer:
        chain = planner.describe_exception(outer)
    assert "RuntimeError: wrapped" in chain and "OSError: connection reset" in chain


async def test_transport_error_history_and_exception_carry_type_and_text(live, monkeypatch) -> None:
    monkeypatch.setattr(planner, "RETRY_DELAY_S", 0)
    fake = use(OSError("socket closed"), OSError("socket closed"), res("{}"))
    with pytest.raises(planner.PlannerFailed) as err:
        await planner.plan_from_text("покажи доску монтажа", operator=OP, project_id=PID)
    assert err.value.detail.startswith("OSError: socket closed") and len(fake.calls) == 2
    rows = [r for r in await history_rows(live, "planner_call") if r.outcome == "transport_error"]
    assert len(rows) == 2 and all("OSError: socket closed" in (r.error or "") for r in rows)


async def test_timeout_retry_gets_a_longer_timeout(live, monkeypatch) -> None:
    monkeypatch.setattr(planner, "RETRY_DELAY_S", 0)
    fake = use(TimeoutError(), res('{"steps":[{"action_id":"montage.get_board","params":{}}],'
                                   '"needs_clarification":null,"explanation_ru":"ok"}'))
    out = await planner.plan_from_text("покажи доску монтажа", operator=OP, project_id=PID)
    assert out.reply.steps
    assert [c["timeout"] for c in fake.calls] == [planner.TIMEOUT_S, planner.TIMEOUT_RETRY_S]
    rows = await history_rows(live, "planner_call")
    assert any("TimeoutError: timeout" in (r.error or "") for r in rows)


def test_eval_row_has_error_detail_and_kinds_summary() -> None:
    import scripts.eval_action_planner as m
    row = {"id": "a", "outcome": "plan", "transport_error": True, "ok": False, "ids_ok": False,
           "error_detail": "ReadTimeout: timeout (timeout_s=60, 60.1s)"}
    assert m.summarize([row])["transport_error_kinds"] == {"ReadTimeout:": 1} or \
        m.summarize([row])["transport_error_kinds"] == {"ReadTimeout": 1}
