"""Несписковые кадры не маскируются под пустой список."""

from __future__ import annotations

from app.orchestrator.validators.grammar import shots_of
from app.orchestrator.validators.preservation import collect_violations


def test_shots_of_non_list_reports_invalid() -> None:
    marker = shots_of({"shots": "нет"})
    assert marker is not None
    assert marker != []
    assert getattr(marker, "invalid", False) is True
    assert shots_of({}) is None
    assert shots_of({"shots": []}) == []
    assert shots_of({"кадры": [{"id": "k"}]}) == [{"id": "k"}]

    before = {"сцены": [{"id": "s1", "номер": 1, "кадры": [{"id": "k1", "закадр": "а"}]}]}
    after = {"сцены": [{"id": "s1", "номер": 1, "кадры": {"shots": "нет"}}]}
    violations = collect_violations(before, after)
    assert any(
        item.code == "kadry_invalid" and item.severity == "warn" and item.id == "s1"
        for item in violations
    )
    assert all(item.severity == "warn" or item.code != "kadry_invalid" for item in violations)
