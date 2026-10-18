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
            "svg": (
                '<svg><script>alert(1)</script><text>кадр</text></svg>'
                '<svg/onload=alert(1)>'
                '<svg href="JavaScript:alert(1)">'
                '<svg><foreignObject>x</foreignObject></svg>'
                '<svg><a href="&#58;alert(1)">'
            ),
        }
    )
    assert "<script" not in html.lower()
    assert "onload=" not in html.lower()
    assert "javascript:" not in html.lower()
    assert "<foreignobject" not in html.lower()
    assert "&#58;" not in html.lower()
    assert "alert(1)" not in html
    assert html.count("<img ") == 1
    assert "data:image/svg+xml;base64," in html


def test_report_iframes_are_sandboxed() -> None:
    for rel in (
        "web/src/components/canvas/node-result-views.tsx",
        "web/src/components/canvas/shots-report-panel.tsx",
    ):
        text = Path(rel).read_text(encoding="utf-8")
        assert 'sandbox="allow-scripts"' in text
        assert "allow-same-origin" not in text
