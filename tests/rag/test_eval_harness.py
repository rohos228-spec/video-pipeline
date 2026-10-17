"""Контур A: E1–E8 зелёные, E8-B не исполняется, фикстуры только replay."""

from __future__ import annotations

from evals.orchestrator.project65 import build_stub, load_dataset
from evals.orchestrator.run_evals import run_contour_a, run_contour_b


def test_seeded_registry_matches_builder() -> None:
    seeded = load_dataset("65-seeded")
    assert seeded == build_stub("65-seeded")
    assert seeded["stub"] is True
    kinds = {}
    for item in seeded["registry"]:
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    assert kinds == {"vo_too_long": 6, "vo_too_short": 6, "frame_deleted": 2}
    assert load_dataset("65-clean")["registry"] == []
    canary = load_dataset("65-canary")
    assert canary["stub"] is True
    assert "Канарейка" in canary["note"]


def test_contour_a_passes_and_e8b_is_deferred() -> None:
    report = run_contour_a()
    assert report["ok"] is True
    assert report["e8_passed"] is True
    assert report["passed"] == report["total"] == 5
    e1 = report["by_id"]["E1"]
    assert e1["details"]["seeded_precision"] == 1.0
    assert e1["details"]["seeded_recall"] == 1.0
    assert e1["details"]["datasets"]["65-clean"]["false_positives"] == []
    assert e1["details"]["canary_scored"] is False
    assert e1["details"]["datasets"]["65-seeded"]["stub"] is True
    for eval_id in ("E4", "E5", "E6", "E8-B"):
        assert report["by_id"][eval_id]["status"] == "deferred"
        assert report["by_id"][eval_id]["passed"] is None
    assert report["by_id"]["E6"]["details"]["labeled_by"] == "rule-derived-placeholder"
    e8 = report["by_id"]["E8"]["details"]["checks"]
    assert all(e8.values())


def test_contour_b_does_not_pretend_live_success() -> None:
    report = run_contour_b(allow_live=False)
    assert report["ok"] is False
    assert report["by_id"]["E8-B"]["status"] == "blocked_no_live"
