"""Повторное ревью #352 №4: флаг переиспользования скелета убран."""

from __future__ import annotations

from pathlib import Path


def test_skeleton_reuse_flag_is_gone_and_report_key_is_safe() -> None:
    text = Path("app/orchestrator/steps/enrich_xlsx.py").read_text(encoding="utf-8")
    assert "_reuse_skeleton.flag" not in text
    assert 'rep["zakadr_overwritten"]' not in text
    assert "rep['zakadr_overwritten']" not in text
    assert text.count("rep.get(") >= 2 and text.count("zakadr_overwritten") >= 2
