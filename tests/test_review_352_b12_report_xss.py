"""П.12: отчёт не исполняет script из текста кадра в same-origin iframe."""

from __future__ import annotations

from pathlib import Path

from app.services.shots_report import _plan_block


def test_plan_svg_script_is_escaped() -> None:
    html = _plan_block(
        {
            "cell": 1,
            "zones": [],
            "passages": [],
            "fixed": [],
            "derived": [],
            "svg": '<svg><script>alert(1)</script><text>кадр</text></svg>',
        }
    )
    assert "<script" not in html.lower()
    assert "alert(1)" not in html


def test_report_iframes_are_sandboxed() -> None:
    for rel in (
        "web/src/components/canvas/node-result-views.tsx",
        "web/src/components/canvas/shots-report-panel.tsx",
    ):
        text = Path(rel).read_text(encoding="utf-8")
        assert 'sandbox="allow-scripts"' in text
        assert "allow-same-origin" not in text
