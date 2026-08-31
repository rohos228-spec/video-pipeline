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


def test_outsee_missing_key_is_media_not_gpt_text() -> None:
    """Hero/Картинки с GPT Image 2 без OUTSEE_API_KEY — не «Текст LLM»."""
    from app.bots.outsee import OutseeImageError

    err = OutseeImageError(
        "OUTSEE_API_KEY пуст — GPT Image 2 / Nano Banana 2 / Veo 3.1 Lite "
        "идут через ключ Outsee",
        context={"error_kind": "no_key", "provider": "outsee"},
    )
    code, msg = describe_error(err)
    assert code == "media_no_key"
    assert "Текст LLM" not in msg
    assert "OUTSEE_API_KEY" in ERROR_CATALOG["media_no_key"].hint


def test_kling_missing_key_is_media_not_gpt_text() -> None:
    from app.bots.kie_kling import KieKlingError

    err = KieKlingError(
        "kie Kling: нет API ключа (KIE_API_KEY / GPT_API_KEY)",
        context={"provider_code": 401, "error_kind": "no_key"},
    )
    code, msg = describe_error(err)
    assert code == "media_no_key"
    assert "Текст LLM" not in msg


def test_outsee_401_is_media_auth_not_gpt_auth() -> None:
    from app.bots.outsee import OutseeImageError

    err = OutseeImageError(
        "outsee 401",
        context={"error_kind": "no_key", "provider": "outsee", "provider_code": 401},
    )
    assert describe_error(err)[0] == "media_no_key"
    err401 = OutseeImageError("outsee forbidden", context={"provider_code": 403})
    assert describe_error(err401)[0] == "media_auth"


def test_message_text_mapping() -> None:
    assert describe_error(Exception("apikey error"))[0] == "gpt_model_unauthorized"
    assert describe_error(Exception("model not register: gpt-5.6"))[0] == "gpt_model_unsupported"
    assert describe_error(Exception("нет JSON vp.check.v1 в ответе"))[0] == "check_no_json"
    assert describe_error(Exception("grsai moderation: violation"))[0] == "media_moderation"
    assert describe_error(FileNotFoundError("нет файла"))[0] == "file_missing"
    assert describe_error(Exception("database is locked"))[0] == "infra_db_locked"


def test_enrich_xlsx_step_error_is_not_excel_file() -> None:
    """Имя шага enrich_xlsx не должно краситься в «Некорректный xlsx»."""
    err = RuntimeError(
        "enrich_xlsx node=n_excel_gpt_fw_frames: L1 call 1 uuid 4f458359: "
        "4 кадров «кабинет следствия» все самостоятельные — "
        "parent = первый кадр этой локации"
    )
    code, msg = describe_error(err)
    assert code != "xlsx_invalid"
    assert "Некорректный xlsx" not in msg
    assert "кабинет следствия" in msg


def test_real_xlsx_file_error_still_maps() -> None:
    assert describe_error(Exception("xlsx-sync: листы не совпали"))[0] == "xlsx_invalid"
    assert describe_error(Exception("project.xlsx не открывается"))[0] == "xlsx_corrupt"


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
