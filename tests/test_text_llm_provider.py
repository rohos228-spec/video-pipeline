"""TEXT_LLM_PROVIDER: TokenRouter/Kimi vs kie/GPT."""

from __future__ import annotations

from app.settings import Settings


def test_tokenrouter_provider_resolves_kimi_settings(monkeypatch) -> None:
    monkeypatch.setenv("TEXT_LLM_PROVIDER", "tokenrouter")
    monkeypatch.setenv("TOKENROUTER_API_KEY", "tr-test-key")
    monkeypatch.setenv("TOKENROUTER_BASE_URL", "https://api.tokenrouter.com/v1")
    monkeypatch.setenv("TOKENROUTER_MODEL", "moonshotai/kimi-k3-free")
    monkeypatch.setenv("GPT_API_KEY", "should-not-use")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("GPT_MODEL", "gpt-5-6-sol")
    s = Settings()
    assert s.resolved_text_llm_provider() == "tokenrouter"
    assert s.text_llm_is_tokenrouter is True
    assert s.gpt_api_effective_key == "tr-test-key"
    assert s.gpt_api_effective_base_url == "https://api.tokenrouter.com/v1"
    assert s.gpt_model_effective == "moonshotai/kimi-k3-free"
    assert s.gpt_chat_path_effective == "/chat/completions"
    assert s.gpt_api_mode_effective == "chat"
    assert "Kimi" in s.text_llm_label
    assert "TokenRouter" in s.text_llm_label
    assert "GPT ·" not in s.text_llm_label


def test_auto_uses_tokenrouter_when_key_present(monkeypatch) -> None:
    monkeypatch.setenv("TEXT_LLM_PROVIDER", "auto")
    monkeypatch.setenv("TOKENROUTER_API_KEY", "tr-auto")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    s = Settings()
    assert s.resolved_text_llm_provider() == "tokenrouter"
    assert s.gpt_api_effective_key == "tr-auto"


def test_kie_provider_keeps_gpt_settings(monkeypatch) -> None:
    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("TOKENROUTER_API_KEY", "tr-ignored")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("GPT_MODEL", "gpt-5-6-sol")
    monkeypatch.setenv("GPT_CHAT_PATH", "/codex/v1/responses")
    monkeypatch.setenv("GPT_API_MODE", "responses")
    s = Settings()
    assert s.resolved_text_llm_provider() == "kie"
    assert s.gpt_api_effective_key == "kie-key"
    assert s.gpt_model_effective == "gpt-5-6-sol"
    assert "responses" in s.gpt_chat_path_effective
    assert "GPT" in s.text_llm_label


def test_chat_url_for_tokenrouter(monkeypatch) -> None:
    monkeypatch.setenv("TEXT_LLM_PROVIDER", "tokenrouter")
    monkeypatch.setenv("TOKENROUTER_API_KEY", "tr-key")
    monkeypatch.setenv("TOKENROUTER_BASE_URL", "https://api.tokenrouter.com/v1")
    monkeypatch.setenv("TOKENROUTER_MODEL", "moonshotai/kimi-k3-free")
    # Reload settings used by gpt_api
    import app.settings as settings_mod
    import app.services.gpt_api as gpt_api

    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(gpt_api, "settings", s)
    url = gpt_api._chat_url(s.gpt_model_effective)
    assert url == "https://api.tokenrouter.com/v1/chat/completions"
    assert gpt_api.is_responses_mode() is False
