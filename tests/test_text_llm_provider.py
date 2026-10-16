"""TEXT_LLM: GPT (kie) default + Vibecode."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.settings import Settings


def test_default_is_kie_gpt(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("GPT_MODEL", "gpt-5-6-sol")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    assert s.resolved_text_llm_provider() == "kie"
    assert s.gpt_api_effective_key == "kie-key"
    assert "GPT" in s.text_llm_label
    assert "Kimi" not in s.text_llm_label


def test_tokenrouter_is_not_selectable(monkeypatch, tmp_path: Path) -> None:
    from app.services import text_llm_catalog as cat

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "tokenrouter")
    monkeypatch.setenv("TOKENROUTER_API_KEY", "tr-test-key")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    assert s.resolved_text_llm_provider() == "kie"
    assert "Kimi" not in s.text_llm_label
    for name in ("tokenrouter", "kimi", "kimi-k3"):
        with pytest.raises(ValueError, match="unknown text LLM provider"):
            cat.write_choice(provider=name, cfg=s)


def test_choice_file_switches_without_touching_gpt_env(
    monkeypatch, tmp_path: Path
) -> None:
    from app.services import text_llm_catalog as cat

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("GPT_MODEL", "gpt-5-6-sol")
    monkeypatch.setenv("VIBECODE_API_KEY", "vk-key")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()

    assert s.resolved_text_llm_provider() == "kie"
    cat.write_choice(provider="vibecode", model_id="gpt-5.6-sol-vibecode", cfg=s)
    assert s.resolved_text_llm_provider() == "vibecode"
    cat.write_choice(provider="kie", model_id="gpt-kie", cfg=s)
    assert s.resolved_text_llm_provider() == "kie"
    assert s.gpt_model == "gpt-5-6-sol"  # GPT_* не трогали


def test_vibecode_models_switch_url_and_key(monkeypatch, tmp_path: Path) -> None:
    from app.services import text_llm_catalog as cat
    import app.settings as settings_mod
    import app.services.gpt_api as gpt_api

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "kie")
    monkeypatch.setenv("GPT_API_KEY", "kie-key")
    monkeypatch.setenv("GPT_BASE_URL", "https://api.kie.ai")
    monkeypatch.setenv("GPT_CHAT_PATH", "/codex/v1/responses")
    monkeypatch.setenv("VIBECODE_API_KEY", "vk-test")
    monkeypatch.setenv("VIBECODE_BASE_URL", "https://vibecode.moe/v1")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(gpt_api, "settings", s)

    cat.write_choice(provider="vibecode", model_id="gpt-5.5-vibecode", cfg=s)
    assert s.resolved_text_llm_provider() == "vibecode"
    assert s.gpt_model_effective == "gpt-5.6-sol"
    assert s.gpt_api_effective_key == "vk-test"
    assert s.gpt_chat_path_effective == "/chat/completions"
    assert gpt_api.is_responses_mode() is False
    assert gpt_api._chat_url(s.gpt_model_effective) == (
        "https://vibecode.moe/v1/chat/completions"
    )

    cat.write_choice(provider="vibecode", model_id="gpt-5.6-sol", cfg=s)
    assert s.gpt_model_effective == "gpt-5.6-sol"
    assert "GPT 5.6 Sol" in s.text_llm_label

    cat.write_choice(provider="kie", model_id="gpt-kie", cfg=s)
    assert s.resolved_text_llm_provider() == "kie"
    assert s.gpt_api_effective_key == "kie-key"
    assert s.gpt_api_effective_base_url == "https://api.kie.ai"


def test_vibecode_uses_vps_relay_when_set(
    monkeypatch, tmp_path: Path
) -> None:
    from app.services import text_llm_catalog as cat
    import app.settings as settings_mod
    import app.services.gpt_api as gpt_api

    monkeypatch.setenv("TEXT_LLM_PROVIDER", "vibecode")
    monkeypatch.setenv("VIBECODE_API_KEY", "vk-test")
    monkeypatch.setenv("VIBECODE_BASE_URL", "https://vibecode.moe/v1")
    monkeypatch.setenv("GPT_BASE_URL", "https://gpt.example.com")
    monkeypatch.setenv("GPT_RELAY_TOKEN", "relay-secret")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    s = Settings()
    monkeypatch.setattr(settings_mod, "settings", s)
    monkeypatch.setattr(gpt_api, "settings", s)
    cat.write_choice(provider="vibecode", model_id="gpt-5.5-vibecode", cfg=s)
    assert s.gpt_api_effective_base_url == "https://gpt.example.com"
    assert gpt_api._chat_url("gpt-5.5") == (
        "https://gpt.example.com/v1/chat/completions"
    )
    headers = gpt_api._headers()
    assert headers["Authorization"] == "Bearer vk-test"
    assert headers["X-VP-Relay-Token"] == "relay-secret"
    cat.write_choice(provider="kie", model_id="gpt-kie", cfg=s)
    assert s.gpt_api_effective_base_url == "https://gpt.example.com"
    assert gpt_api._chat_url("gpt-5-6-sol") == (
        "https://gpt.example.com/codex/v1/responses"
    )


def test_parse_sse_keeps_text_when_upstream_error_follows() -> None:
    from app.services.gpt_api import parse_chat_completions_sse_lines

    lines = [
        'data: {"choices":[{"delta":{"content":"hello"}}]}',
        'data: {"error": {"message": "upstream error", "type": "upstream_error"}}',
        "data: [DONE]",
    ]
    text, finish, _ = parse_chat_completions_sse_lines(lines)
    assert text == "hello"
    assert finish == "stop"


def test_parse_chat_completions_sse() -> None:
    from app.services.gpt_api import parse_chat_completions_sse_lines

    lines = [
        'data: {"choices":[{"delta":{"content":"hel"}}]}',
        'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}]}',
        "data: [DONE]",
    ]
    text, finish, _ = parse_chat_completions_sse_lines(lines)
    assert text == "hello"
    assert finish == "stop"


def test_parse_chat_completions_json_without_data_prefix() -> None:
    from app.services.gpt_api import parse_chat_completions_sse_lines

    lines = [
        '{"choices":[{"message":{"content":"полный промт кадра"},"finish_reason":"stop"}]}',
    ]
    text, finish, _ = parse_chat_completions_sse_lines(lines)
    assert text == "полный промт кадра"
    assert finish == "stop"


def test_parse_chat_completions_message_when_delta_has_only_role() -> None:
    from app.services.gpt_api import parse_chat_completions_sse_lines

    lines = [
        'data: {"choices":[{"delta":{"role":"assistant"},"message":{"content":"сцена"}}]}',
    ]
    text, _, _ = parse_chat_completions_sse_lines(lines)
    assert text == "сцена"


def test_check_provider_envelope_openai_error() -> None:
    from app.services.gpt_api import GptApiError, _check_provider_envelope

    with pytest.raises(GptApiError, match="overloaded") as ei:
        _check_provider_envelope({"error": {"message": "The server is overloaded"}})
    assert ei.value.retryable is True
