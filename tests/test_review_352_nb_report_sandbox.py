"""Неблокирующее: HTML-отчёт в iframe без доступа к API студии."""

from pathlib import Path


def test_shots_report_iframes_are_sandboxed() -> None:
    for rel in (
        "web/src/components/canvas/shots-report-panel.tsx",
        "web/src/components/canvas/node-result-views.tsx",
    ):
        src = Path(rel).read_text(encoding="utf-8")
        assert 'sandbox="allow-scripts"' in src
        assert "allow-same-origin" not in src
