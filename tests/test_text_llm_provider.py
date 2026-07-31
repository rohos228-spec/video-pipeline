"""TEXT_LLM: GPT (kie) default + optional Kimi/TokenRouter."""

from __future__ import annotations

from pathlib import Path

from app.settings import Settings


def test_default_is_kie_gpt_not_tokenrouter(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("TOKENROUTER_API_KEY", "tr-present-but-ignored")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("GPT_MODEL", "gpt-5-6-sol")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    assert s.resolved_text_llm_provider() == "kie"
    assert s.gpt_api_effective_key == "kie-key"
    assert "GPT" in s.text_llm_label
    assert "Kimi" not in s.text_llm_label


def test_tokenrouter_only_when_explicit(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TEXT_LLM_PROVIDER", "tokenrouter")
    monkeypatch.setenv("TOKENROUTER_API_KEY", "tr-test-key")
    monkeypatch.setenv("TOKENROUTER_BASE_URL", "https://api.tokenrouter.com/v1")
    monkeypatch.setenv("TOKENROUTER_MODEL", "moonshotai/kimi-k3-free")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    assert s.resolved_text_llm_provider() == "tokenrouter"
    assert s.gpt_api_effective_key == "tr-test-key"
    assert s.gpt_model_effective == "moonshotai/kimi-k3-free"
    assert s.gpt_chat_path_effective == "/chat/completions"
    assert "Kimi" in s.text_llm_label


def test_choice_file_switches_without_touching_gpt_env(
    monkeypatch, tmp_path: Path
) -> None:
    from app.services import text_llm_catalog as cat

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("TOKENROUTER_API_KEY", "tr-key")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("GPT_MODEL", "gpt-5-6-sol")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()

    assert s.resolved_text_llm_provider() == "kie"
    cat.write_choice(provider="tokenrouter", model_id="kimi-k3-tokenrouter", cfg=s)
    assert s.resolved_text_llm_provider() == "tokenrouter"
    cat.write_choice(provider="kie", model_id="gpt-kie", cfg=s)
    assert s.resolved_text_llm_provider() == "kie"
    assert s.gpt_model == "gpt-5-6-sol"  # GPT_* не трогали


def test_chat_url_for_tokenrouter(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TEXT_LLM_PROVIDER", "tokenrouter")
    monkeypatch.setenv("TOKENROUTER_API_KEY", "tr-key")
    monkeypatch.setenv("TOKENROUTER_BASE_URL", "https://api.tokenrouter.com/v1")
    monkeypatch.setenv("TOKENROUTER_MODEL", "moonshotai/kimi-k3-free")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.settings as settings_mod
    import app.services.gpt_api as gpt_api

    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(gpt_api, "settings", s)
    url = gpt_api._chat_url(s.gpt_model_effective)
    assert url == "https://api.tokenrouter.com/v1/chat/completions"
    assert gpt_api.is_responses_mode() is False
