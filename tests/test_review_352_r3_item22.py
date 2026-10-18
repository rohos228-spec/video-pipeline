"""Ревью #352 п.22: StreamsPicker шлёт только дельту и молчит до загрузки."""

from pathlib import Path

SRC = Path("web/src/components/studio/node-step-params-panel.tsx").read_text(encoding="utf-8")


def test_streams_picker_sends_delta_only() -> None:
    start = SRC.index("export function StreamsPicker")
    body = SRC[start:]
    assert "...meta" not in body
    assert "img_streams: n, outsee_streams: n" in body
    assert "!project.isSuccess" in body
