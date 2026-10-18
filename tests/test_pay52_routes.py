"""52pay: модель из каталога уходит на свой ключ, пустой ключ не перехватывает vibecode."""

from __future__ import annotations

from pathlib import Path

import app.settings as settings_mod
from app.services.llm_override import NodeLlmOverride, use_override
from app.services.pay52_routes import resolve_text, video_api_key
from app.settings import Settings


def _settings(monkeypatch, tmp_path: Path, **env: str) -> Settings:
    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("VIBECODE_API_KEY", "vk-test")
    monkeypatch.setenv("VIBECODE_BASE_URL", "https://vibecode.moe/v1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    fresh = Settings()
    monkeypatch.setattr(settings_mod, "settings", fresh)
    return fresh


def test_blank_key_does_not_route(monkeypatch, tmp_path: Path) -> None:
    fresh = _settings(monkeypatch, tmp_path, PAY52_CLAUDE_HIGH_API_KEY="high-key")
    fresh.pay52_claude_high_api_key = ""
    assert resolve_text("claude-sonnet-5") is None


def test_catalog_models_pick_their_key(monkeypatch, tmp_path: Path) -> None:
    _settings(
        monkeypatch,
        tmp_path,
        PAY52_GPT_API_KEY="gpt-key",
        PAY52_CLAUDE_HIGH_API_KEY="high-key",
        PAY52_CLAUDE_LOW_API_KEY="low-key",
        PAY52_GLM_API_KEY="glm-key",
        PAY52_SEEDANCE_MINI_API_KEY="mini-key",
        PAY52_SEEDANCE_FACES_API_KEY="face-key",
    )
    sol = resolve_text("gpt-5.6-sol")
    assert sol is not None
    assert sol.api_model == "gpt-5.6-sol"
    assert sol.api_key == "gpt-key"
    assert sol.url == "https://api.52pay.com/v1/chat/completions"
    assert sol.headers()["Authorization"] == "Bearer gpt-key"

    sonnet = resolve_text("claude-sonnet-5")
    assert sonnet is not None and sonnet.api_key == "high-key"
    assert sonnet.api_model == "claude-sonnet-5"

    haiku = resolve_text("claude-haiku-4-5")
    assert haiku is not None
    assert haiku.api_key == "low-key"
    assert haiku.api_model == "claude-haiku-4-5-20251001"

    glm = resolve_text("glm-5.3")
    assert glm is not None and glm.api_key == "glm-key"

    assert resolve_text("gpt-5.6-luna") is None
    assert resolve_text("gemini-3.5-flash") is None
    assert video_api_key("seedance-2.0-mini-ref") == "mini-key"
    assert video_api_key("Seedance 2.0 Fast 海外") == "face-key"


def test_pay52_claude_uses_openai_chat_url(monkeypatch, tmp_path: Path) -> None:
    _settings(monkeypatch, tmp_path, PAY52_CLAUDE_HIGH_API_KEY="high-key")
    ov = NodeLlmOverride(
        model_id="claude-sonnet-5",
        channel="stable",
        kind="text",
        provider="vibecode",
        label="Claude Sonnet 5",
    )
    with use_override(ov):
        route = resolve_text(ov.model_id)
        assert route is not None
        assert route.api_key == "high-key"
        assert route.url == "https://api.52pay.com/v1/chat/completions"
