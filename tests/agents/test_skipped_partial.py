"""Непустой skipped без partial=true ядро не считает успехом."""

from app.orchestrator.contracts import r21_result_problem


def test_skipped_requires_partial_true() -> None:
    broken = {"status": "ok", "result": {"skipped": ["n_other"], "partial": False}}
    assert r21_result_problem(broken) == "skipped_without_partial"
    missing = {"status": "ok", "result": {"skipped": ["n_other"]}}
    assert r21_result_problem(missing) == "skipped_without_partial"
    paired = {"status": "ok", "result": {"skipped": ["n_other"], "partial": True}}
    assert r21_result_problem(paired) is None
    only_partial = {"status": "ok", "result": {"skipped": [], "partial": True}}
    assert r21_result_problem(only_partial) == "partial_without_skipped"
