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


def test_apply_ops_unknown_uuid_not_xlsx_invalid() -> None:
    """Прод-инцидент: enrich_xlsx + «неизвестные frame_uuid» — это НЕ xlsx_invalid."""
    exc = RuntimeError(
        "enrich_xlsx node=n_excel_gpt_2: apply-ops отклонён: "
        "неизвестные frame_uuid: ['140c5a40-1111-2222-3333-444455556666']"
    )
    code, msg = describe_error(exc)
    assert code == "apply_ops_unknown_uuid"
    assert code != "xlsx_invalid"
    assert "frame_uuid" in msg


def test_apply_ops_unknown_uuid_direct_error() -> None:
    """Голый ApplyOpsError из db_apply (без обёртки enrich_xlsx) → тот же код."""
    assert describe_error(ValueError("неизвестные frame_uuid: ['abc']"))[0] == (
        "apply_ops_unknown_uuid"
    )
    assert describe_error(RuntimeError("anim_pr apply_ops отклонён: bad frame_uuid x1"))[0] == (
        "apply_ops_unknown_uuid"
    )


def test_apply_ops_rejected_generic() -> None:
    """Прочие apply-ops отказы не должны проваливаться в xlsx_invalid."""
    exc = RuntimeError("enrich_xlsx node=n1: apply-ops отклонён: scenes: нужен id_scene")
    assert describe_error(exc)[0] == "apply_ops_rejected"


def test_plain_xlsx_errors_unchanged() -> None:
    assert describe_error(RuntimeError("project.xlsx: неверная структура листов"))[0] == (
        "xlsx_invalid"
    )
    assert describe_error(RuntimeError("xlsx corrupt: не открывается архив"))[0] == (
        "xlsx_corrupt"
    )
