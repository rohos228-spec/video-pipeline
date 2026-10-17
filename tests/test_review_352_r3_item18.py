"""Ревью #352 п.18: /analyze-style не читает произвольный путь."""

from fastapi import HTTPException
import pytest

from app.web.routers.gen_assistant import _safe_cover_source


def test_analyze_style_rejects_etc_passwd() -> None:
    with pytest.raises(HTTPException) as exc:
        _safe_cover_source("/etc/passwd")
    assert exc.value.status_code == 400
