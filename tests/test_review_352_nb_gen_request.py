"""Неблокирующее: текст запроса помощника не стирается до ответа сервера."""

from pathlib import Path


def test_request_cleared_only_after_successful_fetch() -> None:
    panel = Path("web/src/components/outsee/gen-assistant-panel.tsx").read_text(encoding="utf-8")
    start = panel.split("const startGenerationNow", 1)[1].split("const applyStyleCover", 1)[0]
    assert start.find("await fetch") < start.find('setRequest("")')
    assert start.find("if (!r.ok)") < start.find('setRequest("")')
