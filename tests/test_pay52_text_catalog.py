"""Offline text catalog regressions: 52pay uses the existing vibecode channel."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.text_llm_catalog import (
    CATALOG,
    catalog_api_model,
    catalog_item,
    catalog_status,
    resolve_active_model_id,
    write_choice,
)
from app.settings import Settings

GPT_MODELS = (
    "gpt-5.6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-6-astra",
    "gpt-6-luna",
    "gpt-6-sol",
    "gpt-6.1-sol",
)
CLAUDE_MODELS = (
    "claude-fable-5",
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-sonnet-5",
    "claude-opus-5",
)


@pytest.fixture
def cfg(tmp_path: Path) -> Settings:
    # No dotenv reads and no credentials inherited from the developer's files.
    return Settings(
        _env_file=None,
        DATA_DIR=tmp_path,
        TEXT_LLM_PROVIDER="kie",
        GPT_API_KEY="kie-test-key",
        GPT_RELAY_TOKEN="",
        VIBECODE_BASE_URL="https://api.52pay.com/v1",
        VIBECODE_API_KEY="text-test-key",
        PAY52_API_KEY="different-media-test-key",
    )


@pytest.mark.parametrize("model", GPT_MODELS + CLAUDE_MODELS)
def test_raw_and_suffixed_aliases(model: str) -> None:
    item = catalog_item(model)
    assert item is not None
    assert item["id"] == f"{model}-vibecode"
    assert item["provider"] == "vibecode"
    assert catalog_item(f"{model}-vibecode") is item
    assert catalog_api_model(model) == model
    assert catalog_api_model(f"{model}-vibecode") == model
    assert item["group"] == ("Anthropic" if model.startswith("claude-") else "OpenAI")


def test_catalog_ids_are_unique_and_legacy_alias_survives() -> None:
    ids = [item["id"] for item in CATALOG]
    assert len(ids) == len(set(ids))
    assert catalog_api_model("gpt-5-6-sol") == "gpt-5.6-sol"
    assert catalog_api_model("gpt-5.5") == "gpt-5.5"


@pytest.mark.parametrize("model", GPT_MODELS + CLAUDE_MODELS)
def test_raw_choice_uses_existing_channel(cfg: Settings, model: str) -> None:
    choice = write_choice(provider="vibecode", model_id=model, cfg=cfg)
    assert choice == {"provider": "vibecode", "model_id": f"{model}-vibecode"}
    assert resolve_active_model_id(cfg) == f"{model}-vibecode"
    status = catalog_status(cfg)
    assert status["active_provider"] == "vibecode"
    assert status["active_model"] == model
    assert [item["id"] for item in status["models"] if item["active"]] == [choice["model_id"]]


@pytest.mark.parametrize(
    ("base", "site"),
    [
        ("https://api.52pay.com/v1", "52pay.com"),
        ("https://52pay.com/v1/", "52pay.com"),
        ("https://API.52PAY.COM/v1", "52pay.com"),
        ("https://vibecode.moe/v1", "vibecode.moe"),
        ("https://text.example.test/v1", "text.example.test"),
        ("https://api.52pay.com.evil.test/v1", "api.52pay.com.evil.test"),
    ],
)
def test_site_matches_configured_text_host(cfg: Settings, base: str, site: str) -> None:
    cfg.vibecode_base_url = base
    before = [dict(item) for item in CATALOG]
    rows = catalog_status(cfg)["models"]
    assert all(row["site"] == site for row in rows if row["provider"] == "vibecode")
    assert next(row for row in rows if row["id"] == "gpt-kie")["site"] == "kie.ai"
    assert CATALOG == before


def test_claude_is_catalogued_not_verified(cfg: Settings) -> None:
    rows = {row["model"]: row for row in catalog_status(cfg)["models"]}
    for model in CLAUDE_MODELS:
        assert rows[model]["key_configured"] is True
        assert rows[model]["availability"] == "unknown"
        assert rows[model]["site"] == "52pay.com"


def test_media_key_does_not_configure_text_channel(cfg: Settings) -> None:
    cfg.vibecode_api_key = ""
    rows = catalog_status(cfg)["models"]
    assert all(not row["key_configured"] for row in rows if row["provider"] == "vibecode")
    assert next(row for row in rows if row["id"] == "gpt-kie")["key_configured"] is True


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "claude-sonnet-5"])
def test_header_choice_routes_52pay_with_text_key(
    cfg: Settings, monkeypatch, model: str
) -> None:
    import app.services.gpt_api as gpt_api
    import app.settings as settings_mod

    monkeypatch.setattr(settings_mod, "settings", cfg)
    monkeypatch.setattr(gpt_api, "settings", cfg)
    write_choice(provider="vibecode", model_id="gpt-6.1-sol", cfg=cfg)
    assert cfg.gpt_api_effective_key == "text-test-key"
    assert gpt_api._headers()["Authorization"] == "Bearer text-test-key"
    assert gpt_api._chat_url("gpt-6.1-sol") == "https://api.52pay.com/v1/chat/completions"
    cfg.vibecode_api_key = ""
    with pytest.raises(gpt_api.GptApiError, match="VIBECODE_API_KEY"):
        gpt_api._headers()


def test_status_does_not_change_default_or_write_choice(cfg: Settings) -> None:
    status = catalog_status(cfg)
    assert status["active_provider"] == "kie"
    assert status["active_model"] == cfg.gpt_model
    assert not (Path(cfg.data_dir) / "text_llm_choice.json").exists()
