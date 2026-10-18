"""p95 прод-пути читает только победителя dev-v2 и сверяет его с профилем."""

from __future__ import annotations

from evals.orchestrator.dev_v2_p95 import profile_matches, profile_view


def test_profile_match_is_the_current_constants() -> None:
    view = profile_view()
    assert profile_matches({**view, "rerank": True, "key": "current"})
    changed = {**view, "rerank": True, "ndcg_at_8": 0.9, "family_boost": view["family_boost"] + 1}
    assert profile_matches(changed) is False
    assert profile_matches({**view, "rerank": False}) is False


def test_p95_module_does_not_name_a_holdout() -> None:
    from pathlib import Path

    source = Path("evals/orchestrator/dev_v2_p95.py").read_text(encoding="utf-8")
    assert "holdout" not in source
