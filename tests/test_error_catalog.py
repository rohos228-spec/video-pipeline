"""Каталог ошибок: маппинг исключений → (code, message)."""

from __future__ import annotations

from app.services.error_catalog import (
    ERROR_CATALOG,
    all_error_codes,
    describe_error,
)
from app.services.gpt_api import GptApiError


def test_catalog_specs_wellformed() -> None:
    assert "unknown" in ERROR_CATALOG
    for code, spec in ERROR_CATALOG.items():
        assert spec.code == code
        assert spec.title and spec.hint


def test_gpt_context_mapping() -> None:
    assert describe_error(GptApiError("x", context={"error_kind": "no_key"}))[0] == "gpt_no_key"
    assert describe_error(GptApiError("x", context={"error_kind": "timeout"}))[0] == "gpt_timeout"
    assert describe_error(GptApiError("x", context={"status_code": 401}))[0] == "gpt_auth"
    assert describe_error(GptApiError("x", context={"status_code": 429}))[0] == "gpt_rate_limit"
    assert describe_error(GptApiError("x", context={"status_code": 503}))[0] == "gpt_server"
    assert describe_error(GptApiError("x", context={"provider_code": 401}))[0] == "gpt_auth"


def test_message_text_mapping() -> None:
    assert describe_error(Exception("apikey error"))[0] == "gpt_model_unauthorized"
    assert describe_error(Exception("model not register: gpt-5.6"))[0] == "gpt_model_unsupported"
    assert describe_error(Exception("нет JSON vp.check.v1 в ответе"))[0] == "check_no_json"
    assert describe_error(Exception("grsai moderation: violation"))[0] == "media_moderation"
    assert describe_error(FileNotFoundError("нет файла"))[0] == "file_missing"
    assert describe_error(Exception("database is locked"))[0] == "infra_db_locked"


def test_unknown_fallback() -> None:
    code, msg = describe_error(Exception("что-то странное"))
    assert code == "unknown"
    assert "странное" in msg


def test_message_format_prefixed() -> None:
    code, msg = describe_error(Exception("apikey error"))
    assert code == "gpt_model_unauthorized"
    assert ERROR_CATALOG[code].title in msg


def test_all_codes_listed() -> None:
    codes = all_error_codes()
    assert "gpt_timeout" in codes and "media_download" in codes and "unknown" in codes
