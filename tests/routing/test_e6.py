"""DoD E6: детерминированные строки меряются отдельно от replay-фикстур."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.orchestrator.routing.types import FixtureMiss
from tests.routing.evals.run_e6 import REPORT, evaluate, write_report

FIXTURES = Path(__file__).with_name("evals") / "fixtures"


async def test_e6_splits_deterministic_and_classifier_accuracy(tmp_path):
    committed = REPORT.read_bytes()
    report = await evaluate(FIXTURES)
    out = tmp_path / "e6_report.md"
    write_report(report, out)
    assert REPORT.read_bytes() == committed
    assert len(report.deterministic_rows) == 36
    assert report.deterministic_accuracy == 1.0
    assert len(report.classifier_rows) == 8
    assert report.simple_on_fast >= 0.90
    assert report.plans_on_strong == 1.0
    assert 0 < report.deterministic_share < 1
    text = out.read_text(encoding="utf-8")
    assert "36/36" in text
    assert "44/44" not in text
    missed = [row.id for row in report.deterministic_rows if not row.ok]
    assert missed == []


async def test_e6_classifier_fixture_miss_is_fixture_miss(tmp_path):
    with pytest.raises(FixtureMiss, match="fixture-miss"):
        await evaluate(tmp_path)
