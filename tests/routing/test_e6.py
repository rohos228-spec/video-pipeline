"""DoD E6: ≥ 90% простых на fast, 100% планов на strong."""

from __future__ import annotations

from tests.routing.evals.run_e6 import REPORT, evaluate, write_report


async def test_e6_accuracy_and_deterministic_share(tmp_path):
    report = await evaluate(tmp_path / "fixtures")
    write_report(report, REPORT)
    assert report.n == 44
    assert report.accuracy >= 0.90
    assert report.simple_on_fast >= 0.90
    assert report.plans_on_strong == 1.0
    assert 0 < report.deterministic_share < 1
    missed = [row.id for row in report.rows if not row.ok]
    assert missed == []
