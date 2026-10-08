"""W5-5 / D14 (подготовка): скрипт оценки считает метрики отдельно по отложенной выборке. Живого прогона нет."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import eval_action_planner as ev  # noqa: E402


def plan_case(cid: str, aid: str, set_name: str) -> dict:
    return {"id": cid, "text": cid, "outcome": "plan", "set": set_name,
            "steps": [{"action_id": aid, "params": {}}]}


def test_score_case_carries_set_and_flags_transport_error() -> None:
    case = plan_case("a", "montage.get_board", "heldout")
    ok = ev.score_case(case, [{"action_id": "montage.get_board", "params": {}}], None, None)
    assert ok["set"] == "heldout" and ok["ok"] and ok["transport_error"] is False
    lost = ev.score_case(case, [], None, "transport_error")
    assert lost["transport_error"] is True and not lost["ok"]
    other = ev.score_case(case, [], None, "planner_bad_output")
    assert other["transport_error"] is False
    assert ev.score_case({k: v for k, v in case.items() if k != "set"}, [], None, None)["set"] == "main"


def test_summaries_are_split_by_set_and_transport_errors_are_separate() -> None:
    rows = []
    for i in range(10):  # основной набор: 9 верно, 1 неверно
        rows.append(ev.score_case(plan_case(f"m{i}", "montage.get_board", "main"),
                                  [{"action_id": "montage.get_board" if i else "project.stop", "params": {}}],
                                  None, None))
    held = [("h0", "montage.get_board", True), ("h1", "montage.get_board", True),
            ("h2", "montage.get_board", False), ("h3", "montage.get_board", None)]
    for cid, aid, good in held:
        case = plan_case(cid, aid, "heldout")
        if good is None:  # обрыв сети
            rows.append(ev.score_case(case, [], None, "transport_error"))
        else:
            rows.append(ev.score_case(case, [{"action_id": aid if good else "project.stop", "params": {}}],
                                      None, None))
    by = ev.summarize_by_set(rows)
    assert set(by) == {"main", "heldout"}
    assert by["main"]["requests"] == 10 and by["main"]["plan_ids_exact"] == 0.9
    assert by["heldout"]["requests"] == 4 and by["heldout"]["transport_errors"] == 1
    assert by["heldout"]["plan_ids_exact"] == 0.5  # 2 из 4, обрыв считается промахом
    assert by["heldout"]["plan_ids_exact_no_transport"] == 0.667  # 2 из 3 без обрыва
    assert by["heldout"]["failed_ids"] == ["h2", "h3"]
    total = ev.summarize(rows)
    assert total["transport_errors"] == 1 and total["requests"] == 14


def test_summarize_keeps_old_keys() -> None:
    row = ev.score_case(plan_case("a", "montage.get_board", "main"),
                        [{"action_id": "montage.get_board", "params": {}}], None, None)
    out = ev.summarize([row])
    for key in ("requests", "accuracy", "plan_ids_exact", "plan_params_ok", "clarify_ok", "refuse_ok",
                "prompt_tokens", "completion_tokens", "est_cost_usd", "failed_ids"):
        assert key in out
    assert ev.summarize([])["requests"] == 0


def test_select_only_works_on_the_chosen_set() -> None:
    held = ev.load_selected("heldout")
    picked = ev.select_cases(held, "ho_001,ho_002", 5)
    assert [c["id"] for c in picked] == ["ho_001", "ho_002"] and all(c["set"] == "heldout" for c in picked)
    sample = ev.select_cases(held, "", 12)
    assert len(sample) == 12 and len({c["workflow"] for c in sample}) > 6
    import pytest
    with pytest.raises(ValueError):
        ev.select_cases(held, "ho_001,e001_not_here", 5)


def test_live_flag_is_still_required(capsys) -> None:
    import asyncio
    sys.argv = ["eval_action_planner.py", "--env-file", "x", "--set", "heldout"]
    assert asyncio.run(ev.main()) == 2
    assert "needs --live" in capsys.readouterr().out
