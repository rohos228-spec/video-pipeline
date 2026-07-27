"""gpt_client / xlsx_gpt_flow API transport (без CDP)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.gpt_api import GptChatResult
from app.services.gpt_client import ApiGptClient, GptApiUnavailable, require_gpt_api


def test_require_gpt_api_raises_without_key(monkeypatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "gpt_api_key", "")
    monkeypatch.setattr(settings, "grsai_api_key", "")
    with pytest.raises(GptApiUnavailable):
        require_gpt_api()


@pytest.mark.asyncio
async def test_api_client_ask_fresh(monkeypatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "gpt_api_key", "k")
    monkeypatch.setattr(settings, "gpt_base_url", "https://api.kie.ai")

    async def fake_chat(**kwargs):
        assert kwargs["prompt"] == "привет"
        return GptChatResult(text="ок", model="gpt-5-6-sol", finish_reason="completed")

    monkeypatch.setattr("app.services.gpt_api.chat", fake_chat)
    client = ApiGptClient()
    reply = await client.ask_fresh("привет")
    assert reply == "ок"


@pytest.mark.asyncio
async def test_xlsx_flow_ask_and_download_txt(monkeypatch, tmp_path: Path) -> None:
    from app.settings import settings
    from app.services import xlsx_gpt_flow as xgf

    monkeypatch.setattr(settings, "gpt_api_key", "k")
    monkeypatch.setattr(settings, "gpt_base_url", "https://api.kie.ai")

    prompt = tmp_path / "prompt.txt"
    prompt.write_text("мастер", encoding="utf-8")
    xlsx = tmp_path / "project.xlsx"
    from openpyxl import Workbook

    Workbook().save(xlsx)
    out = tmp_path / "voiceover.txt"

    async def fake_chat(**kwargs):
        return GptChatResult(
            text="закадровый текст ролика достаточно длинный",
            model="m",
            finish_reason="completed",
        )

    monkeypatch.setattr("app.services.gpt_api.chat", fake_chat)
    reply = await xgf.telegram_style_ask_and_download(
        "сделай voiceover",
        [prompt, xlsx],
        out,
        ask_timeout=30,
        download_timeout=30,
        allow_reply_text_fallback=True,
    )
    assert "закадровый" in reply
    assert out.exists() and out.stat().st_size > 10


@pytest.mark.asyncio
async def test_xlsx_flow_no_browser_import(monkeypatch) -> None:
    """Хабу нельзя тянуть CDP для ask."""
    import app.services.xlsx_gpt_flow as xgf

    src = Path(xgf.__file__).read_text(encoding="utf-8")
    assert "browser_session" not in src
    assert "ChatGPTBot" not in src
