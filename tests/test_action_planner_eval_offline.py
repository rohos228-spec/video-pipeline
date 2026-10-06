"""Офлайн-оценка предфильтра каталога планировщика: без модели и без сети.

Для каждого запроса из tests/fixtures/action_planner_eval.json проверяется, что в промпт попали
ВСЕ ожидаемые действия (полнота 100%), а запросы вне каталога не показывают платных и
разрушающих возможностей. Печатает размеры (запустить с -s, чтобы увидеть отчёт).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.action_layer import planner, planner_catalog
from app.services.action_layer.spec import ensure_builtin_actions, registry

FIXTURE = Path(__file__).parent / "fixtures" / "action_planner_eval.json"
CASES = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
MAX_CATALOG = 24
OLD_FULL_TOKENS = 8500  # замер живого запроса с полным каталогом (56 действий)


def facts_of(case: dict) -> dict:
    ctx = case.get("context") or {}
    return {"project_id": 1, "selected_scene": ctx.get("selected_scene"),
            "selected_frame": ctx.get("selected_frame"), "status": ctx.get("status", "idle"),
            "frame_count": ctx.get("frame_count", 10), "max_frame_number": 10, "nodes": []}


def select(case: dict, n: int = MAX_CATALOG):
    ensure_builtin_actions()
    return planner_catalog.select_catalog(case["text"], facts_of(case), registry, n)


def expected_ids(case: dict) -> set[str]:
    return {s["action_id"] for s in case["steps"]} | set(case.get("also_offered", []))


def test_fixture_is_large_and_covers_workflows() -> None:
    assert len(CASES) >= 200
    kinds = {c["outcome"] for c in CASES}
    assert kinds == {"plan", "clarify", "refuse"}
    wfs = {c["workflow"] for c in CASES}
    for need in ("read board", "edit voiceover", "swap", "queue regen", "split scene", "merge scenes",
                 "improve scene", "delete frame", "assemble", "align audio", "node settings",
                 "group insert", "ambiguous", "out_of_catalog"):
        assert need in wfs, need
    ensure_builtin_actions()
    for c in CASES:
        for aid in expected_ids(c):
            assert registry.find(aid) is not None, (c["id"], aid)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_prefilter_recall_is_total(case) -> None:
    sel = select(case)
    missing = expected_ids(case) - sel.offered
    assert not missing, f"{case['id']}: not offered {sorted(missing)}; offered={sel.ids}"
    assert len(sel.ids) <= MAX_CATALOG
    assert {"montage.get_board", "project.stop"} <= sel.offered


@pytest.mark.parametrize("case", [c for c in CASES if c["outcome"] == "refuse"],
                         ids=lambda c: c["id"])
def test_out_of_catalog_never_surfaces_dangerous_actions(case) -> None:
    sel = select(case)
    risky = [a for a in sel.ids if registry.get(a).risk in ("paid", "destructive")]
    assert not risky, f"{case['id']} offered {risky}"
    if sel.out_of_scope:
        assert sel.no_match and len(sel.ids) <= 8


def test_no_match_uses_small_safe_set_not_full_catalog() -> None:
    sel = planner_catalog.select_catalog("абракадабра квиз", {"project_id": 1}, registry, MAX_CATALOG)
    assert sel.no_match and len(sel.ids) <= 6
    assert all(registry.get(a).risk in ("read", "local_safe") for a in sel.ids)


def test_filter_disabled_when_limit_covers_registry() -> None:
    ensure_builtin_actions()
    sel = planner_catalog.select_catalog("что угодно", {}, registry, len(registry))
    assert not sel.filtered and len(sel.ids) == len(registry)


def test_selection_is_deterministic() -> None:
    a = select(CASES[5]).ids
    assert a == select(CASES[5]).ids


def test_report_precision_and_prompt_size(capsys) -> None:
    ensure_builtin_actions()
    ctx = {"facts": facts_of({}), "version": "v"}
    old = planner_catalog.payload_chars({
        "catalog": planner_catalog.full_entries(registry), "project_context": ctx["facts"],
        "limits": {"max_steps": 20}, "user_request": "x"}) + len(planner.SYSTEM)
    counts, chars, hits, extra = [], [], 0, 0
    for c in CASES:
        _msgs, info = planner.build_prompt(c["text"], {"facts": facts_of(c), "version": "v"},
                                           registry, 20, MAX_CATALOG)
        counts.append(len(info["offered"]))
        chars.append(info["chars"])
        exp = expected_ids(c)
        hits += len(exp & set(info["offered"]))
        extra += len(set(info["offered"]) - exp)
    avg_n = sum(counts) / len(counts)
    avg_chars = sum(chars) / len(chars)
    est_tokens = avg_chars / old * OLD_FULL_TOKENS
    precision = hits / max(1, hits + extra)
    with capsys.disabled():
        print(f"\n[eval] cases={len(CASES)} registry={len(registry)} avg_offered={avg_n:.1f} "
              f"max_offered={max(counts)} precision={precision:.2f} "
              f"old_prompt_chars={old} avg_new_chars={avg_chars:.0f} "
              f"est_tokens_old={OLD_FULL_TOKENS} est_tokens_new={est_tokens:.0f} "
              f"reduction={1 - est_tokens / OLD_FULL_TOKENS:.0%}")
    assert max(counts) <= MAX_CATALOG
    assert avg_chars < 0.4 * old


def _script():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "eval_action_planner", Path(__file__).parent.parent / "scripts" / "eval_action_planner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_live_script_offline_helpers() -> None:
    mod = _script()
    cases = mod.load_cases()
    assert len(cases) == len(CASES)
    pick = mod.sample_cases(cases, 10)
    assert len(pick) == 10 and len({c["id"] for c in pick}) == 10
    assert pick == mod.sample_cases(cases, 10)
    plan = next(c for c in cases if c["id"] == "swap_slots")
    good = [{"action_id": "montage.swap_slots",
             "params": {"project_id": 2, "kind": "image", "a_frame": 2, "b_frame": 3, "a_shot": 1,
                        "b_shot": 1}}]
    assert mod.score_case(plan, good, None, None)["ok"]
    bad = [{"action_id": "montage.swap_slots", "params": {"project_id": 2, "kind": "video"}}]
    r = mod.score_case(plan, bad, None, None)
    assert r["ids_ok"] and not r["params_ok"]
    assert mod.score_case(next(c for c in cases if c["id"] == "ref1"), [], None, "planner_rejected")["ok"]
    assert not mod.score_case(next(c for c in cases if c["id"] == "amb1"), [], None, None)["ok"]
    assert mod.summarize([r])["requests"] == 1


def test_live_script_refuses_without_live_flag_and_reads_only_whitelisted_env(tmp_path) -> None:
    import subprocess
    import sys
    script = Path(__file__).parent.parent / "scripts" / "eval_action_planner.py"
    env_file = tmp_path / "e.env"
    env_file.write_text("VIBECODE_API_KEY=k\nOTHER_SECRET=zzz\nGPT_BASE_URL=u\n", encoding="utf-8")
    mod = _script()
    assert mod.load_env(str(env_file)) == {"VIBECODE_API_KEY": "k", "GPT_BASE_URL": "u"}
    p = subprocess.run([sys.executable, str(script), "--env-file", str(env_file), "--seed-data", str(tmp_path)],
                       capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 2 and "needs --live" in p.stdout
