import re
from pathlib import Path

import pytest

from app.services.access_guard import path_needs_operator_token


@pytest.mark.parametrize("path", [
    "/api/projects/7/resume", "/api/projects/my-slug/continue", "/api/hitl/12/decision",
])
def test_resume_continue_and_hitl_decision_need_token(path):
    assert path_needs_operator_token(path, "POST")
    assert not path_needs_operator_token(path, "GET")


def test_telegram_bot_does_not_call_http_api():
    text = (Path(__file__).resolve().parent.parent / "app" / "telegram" / "bot.py").read_text(encoding="utf-8")
    assert not re.search(r"(httpx|requests)\.(get|post|put|patch|delete|AsyncClient|Client)|http://127\.0\.0\.1|/api/projects/", text)
