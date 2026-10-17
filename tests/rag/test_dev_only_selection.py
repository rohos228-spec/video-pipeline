"""Подбор конфига видит все 50 старых id и не видит holdout."""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.orchestrator.holdout import HoldoutLeakError
from evals.orchestrator.precision_live import (
    SelectionLeakError,
    dev_only_queries,
    require_dev_only,
    select_on_dev,
)


def test_holdout_id_cannot_enter_selection() -> None:
    with pytest.raises(SelectionLeakError):
        require_dev_only(["live-01", "hold-01"])
    with pytest.raises(SelectionLeakError):
        select_on_dev(
            [("fit", "fit", {"ndcg_at_8": 0.7, "hit_at_5": 0.95, "p95_s": 0.2})],
            ["hold-02"],
        )


def test_former_test_ids_are_dev_plus() -> None:
    ids = require_dev_only(["live-01", "live-02", "live-50"])
    assert ids == ["live-01", "live-02", "live-50"]
    chosen = dev_only_queries(
        [
            {"id": "live-01", "text": "камера"},
            {"id": "live-02", "text": "бывший test"},
            {"id": "live-29", "text": "сборка"},
        ]
    )
    assert [query["id"] for query in chosen] == ["live-01", "live-02", "live-29"]
    with pytest.raises(HoldoutLeakError):
        dev_only_queries([{"id": "hold-01", "text": "отложенный"}])
    key, _why = select_on_dev(
        [("fit", "fit", {"ndcg_at_8": 0.7, "hit_at_5": 0.95, "p95_s": 0.2})],
        [query["id"] for query in chosen],
    )
    assert key == "fit"


def test_selection_entry_points_call_the_dev_guard() -> None:
    source = Path("evals/orchestrator/precision_live.py").read_text(encoding="utf-8")
    for name in ("def _main_grid", "def run_profile_pair", "def run_abc", "def run_tune"):
        start = source.index(name)
        nxt = source.find("\ndef ", start + 1)
        body = source[start:nxt]
        assert "read_for_tuning" in body
        assert "dev_only_queries" in body or "require_dev_only" in body
