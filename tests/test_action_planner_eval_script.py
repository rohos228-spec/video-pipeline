from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "eval_action_planner.py"


def script():
    spec = importlib.util.spec_from_file_location("eval_action_planner", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_shard_parsing_and_disjoint_cover() -> None:
    m = script()
    assert m.parse_shard("1/1") == (1, 1) and m.parse_shard("3/4") == (3, 4)
    for bad in ("0/3", "4/3", "x", "1/0", "2", "a/b", "-1/3"):
        with pytest.raises(ValueError):
            m.parse_shard(bad)
    cases = [{"id": str(i)} for i in range(23)]
    parts = [m.shard_cases(cases, i, 4) for i in range(1, 5)]
    flat = [c["id"] for p in parts for c in p]
    assert sorted(flat, key=int) == [c["id"] for c in cases] and len(set(flat)) == 23
    assert max(len(p) for p in parts) - min(len(p) for p in parts) <= 1
    assert m.shard_cases(cases, 1, 1) == cases


def test_resolve_vars_replaces_only_whole_known_placeholders() -> None:
    m = script()
    v = {"frame.5": 1205, "proposal.3.id": "abc"}
    got = m.resolve_vars([{"action_id": "a", "params": {"frame_id": "$frame.5", "ids": ["$proposal.3.id", "$x.1"],
                                                       "text": "цена $frame.5"}}], v)
    assert got[0]["params"] == {"frame_id": 1205, "ids": ["abc", "$x.1"], "text": "цена $frame.5"}


def test_score_case_resolves_placeholders_and_checks_extra_expectations() -> None:
    m = script()
    plan = {"id": "p", "outcome": "plan", "steps": [{"action_id": "montage.delete_frame",
                                                    "params": {"frame_id": "$frame.22"}}],
            "explanation_contains": ["по умолчанию"]}
    step = [{"action_id": "montage.delete_frame", "params": {"project_id": 2, "frame_id": 1222}}]
    ok = m.score_case(plan, step, None, None, "Удаляю. По умолчанию подставлено: x=1.", {"frame.22": 1222})
    assert ok["ok"] and ok["explained_ok"]
    assert not m.score_case(plan, step, None, None, "Удаляю.", {"frame.22": 1222})["ok"]
    assert not m.score_case(plan, step, None, None, "По умолчанию.", {"frame.22": 1})["ok"]
    assert not m.score_case(plan, step, None, None, "По умолчанию.", None)["ok"]
    up = {"id": "u", "outcome": "clarify", "steps": [], "clarify_contains": ["прикрепите файл"]}
    assert m.score_case(up, [], "Прикрепите файл кнопкой загрузки. Он нужен для ...", None)["ok"]
    assert not m.score_case(up, [], "Укажите путь к файлу", None)["ok"]
    assert not m.score_case(up, step, "Прикрепите файл", None)["ok"]
    plain = {"id": "c", "outcome": "clarify", "steps": []}
    assert m.score_case(plain, [], "Что именно?", None)["ok"] and not m.score_case(plain, [], None, None)["ok"]


def test_budget_never_overshoots_when_every_request_costs_the_assumed_amount() -> None:
    m = script()
    b = m.SpendBudget(0.07, floor_usd=0.02)
    assert [b.try_start() for _ in range(4)] == [True, True, True, False]
    for _ in range(3):
        b.finish(0.02)
    assert b.spent == pytest.approx(0.06) and b.stopped and not b.try_start()
    c = m.SpendBudget(1.0, floor_usd=0.01)
    assert c.try_start()
    c.finish(0.5)
    assert c.try_start() and not c.try_start()


def run(coro):
    return asyncio.run(coro)


def test_run_cases_limits_concurrency_and_keeps_case_order() -> None:
    m = script()
    state = {"now": 0, "max": 0}

    async def one(case):
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        await asyncio.sleep(0.01 * (5 - case["n"] % 5))
        state["now"] -= 1
        return {"id": case["id"], "est_cost_usd": 0.001, "ok": True}

    cases = [{"id": f"c{i}", "n": i} for i in range(12)]
    seen = []
    rows = run(m.run_cases(cases, one, concurrency=3, budget=m.SpendBudget(10.0), on_row=seen.append))
    assert [r["id"] for r in rows] == [c["id"] for c in cases] and len(seen) == 12
    assert state["max"] == 3


def test_run_cases_stops_starting_new_requests_at_the_cap() -> None:
    m = script()
    started = []

    async def one(case):
        started.append(case["id"])
        await asyncio.sleep(0.01)
        return {"id": case["id"], "est_cost_usd": 0.02}

    budget = m.SpendBudget(0.07, floor_usd=0.02)
    cases = [{"id": f"c{i}"} for i in range(10)]
    rows = run(m.run_cases(cases, one, concurrency=4, budget=budget))
    assert len(rows) == 3 and budget.stopped and budget.spent <= 0.07 + 1e-9
    assert started == ["c0", "c1", "c2"]


def test_run_cases_releases_the_slot_when_a_case_raises() -> None:
    m = script()

    async def one(case):
        if case["id"] == "boom":
            raise RuntimeError("x")
        return {"id": case["id"], "est_cost_usd": 0.0}

    budget = m.SpendBudget(10.0)
    with pytest.raises(RuntimeError):
        run(m.run_cases([{"id": "boom"}, {"id": "ok"}], one, concurrency=1, budget=budget))
    assert budget.inflight == 0


def test_summary_lists_failed_ids_and_counts_clarify() -> None:
    m = script()
    rows = [{"id": "a", "outcome": "plan", "ok": True, "ids_ok": True, "params_ok": True},
            {"id": "b", "outcome": "plan", "ok": False, "ids_ok": True, "params_ok": False},
            {"id": "c", "outcome": "clarify", "ok": True}]
    s = m.summarize(rows)
    assert s["failed_ids"] == ["b"] and s["requests"] == 3 and s["clarify_ok"] == [True]


def test_script_options_and_rate_limit_override_are_in_place(tmp_path) -> None:
    help_text = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True,
                               encoding="utf-8").stdout
    for flag in ("--shard", "--concurrency", "--seed-realistic", "--no-seed-realistic", "--max-usd",
                 "--max-requests", "--est-usd"):
        assert flag in help_text, flag
    src = SCRIPT.read_text(encoding="utf-8")
    rate = src.index('os.environ["ACTION_LAYER_PLANNER_CALLS_PER_HOUR"]')
    assert rate < src.index("from app.main import _init_db")
    env_file = tmp_path / "e.env"
    env_file.write_text("VIBECODE_API_KEY=k\n", encoding="utf-8")
    p = subprocess.run([sys.executable, str(SCRIPT), "--live", "--env-file", str(env_file), "--shard", "5/3"],
                       capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 2 and "--shard" in p.stdout
    p = subprocess.run([sys.executable, str(SCRIPT), "--env-file", str(env_file)],
                       capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 2 and "needs --live" in p.stdout


def test_only_selects_exact_cases_in_fixture_order_and_rejects_unknown_ids() -> None:
    m = script()
    cases = m.load_cases()
    got = m.select_cases(cases, "swap_shots, board_0 ,read_board", 3)
    assert [c["id"] for c in got] == [c["id"] for c in cases if c["id"] in {"swap_shots", "board_0", "read_board"}]
    assert len(m.select_cases(cases, "", 10)) == 10
    assert len(m.select_cases(cases, "read_board", 0)) == 1
    with pytest.raises(ValueError, match="nope"):
        m.select_cases(cases, "read_board,nope", 10)


def test_clarify_words_may_come_from_the_question_options_or_explanation() -> None:
    m = script()
    case = {"id": "d", "outcome": "clarify", "steps": [], "clarify_contains": ["шот"]}
    assert not m.score_case(case, [], "Какую картинку удалить?", None)["ok"]
    assert m.score_case(case, [], "Какую картинку удалить?", None, "Файлы в обоих шотах.")["ok"]
    assert m.score_case(case, [], "Какую картинку удалить?", None, None, None, ["шот 1", "шот 2"])["ok"]


def test_only_flag_is_documented_and_unknown_ids_stop_before_any_request(tmp_path) -> None:
    help_text = subprocess.run([sys.executable, str(SCRIPT), "--help"], capture_output=True, text=True,
                               encoding="utf-8").stdout
    assert "--only" in help_text
    env_file = tmp_path / "e.env"
    env_file.write_text("VIBECODE_API_KEY=k\n", encoding="utf-8")
    p = subprocess.run([sys.executable, str(SCRIPT), "--live", "--env-file", str(env_file), "--only", "nope"],
                       capture_output=True, text=True, encoding="utf-8")
    assert p.returncode == 2 and "unknown case ids" in p.stdout
