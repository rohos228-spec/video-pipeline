"""Каталог возможных ошибок пайплайна (API-режим, без Chrome).

Централизует все известные ошибки, чтобы:
  * писать в `NodeRun.error` короткие понятные сообщения (красная нода),
  * не терять/не «глотать» ошибки — у каждой есть код и человекочитаемый текст.

`describe_error(exc)` возвращает (code, message) для любого исключения:
известные маппятся в каталог, неизвестные → ("unknown", str(exc)).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErrorSpec:
    code: str
    title: str          # короткий заголовок для ноды
    hint: str           # что делать


# ── Полный каталог возможных ошибок ────────────────────────────────────────
# Ключ = стабильный код. Группы: gpt_* (текстовый API), media_* (grsai
# картинки/видео), file_*, check_* (vp.check.v1), pipeline_*, infra_*.
ERROR_CATALOG: dict[str, ErrorSpec] = {
    # ── GPT текстовый API (app/services/gpt_api.py) ──
    "gpt_no_key": ErrorSpec(
        "gpt_no_key",
        "Текст LLM: нет ключа",
        "Нода: VIBECODE_API_KEY. Шапка kie: GPT_API_KEY. Kimi: TOKENROUTER_API_KEY.",
    ),
    "gpt_no_base": ErrorSpec(
        "gpt_no_base",
        "Текст LLM: нет базы",
        "Kimi: TOKENROUTER_BASE_URL. GPT: GPT_BASE_URL.",
    ),
    "gpt_auth": ErrorSpec(
        "gpt_auth",
        "Текст LLM: неверный ключ (401/403)",
        "Проверь TOKENROUTER_API_KEY или GPT_API_KEY.",
    ),
    "gpt_model_unauthorized": ErrorSpec(
        "gpt_model_unauthorized",
        "Текст LLM: модель не авторизована",
        "Ключ без доступа к модели (Kimi/GPT).",
    ),
    "gpt_model_unsupported": ErrorSpec(
        "gpt_model_unsupported",
        "Текст LLM: модель не поддерживается",
        "Проверь TOKENROUTER_MODEL или GPT_MODEL.",
    ),
    "gpt_rate_limit": ErrorSpec("gpt_rate_limit", "GPT: лимит запросов (429)", "Снизь параллельность/подожди."),
    "gpt_server": ErrorSpec("gpt_server", "GPT: сбой шлюза (5xx)", "Временная ошибка провайдера, повтор."),
    "gpt_timeout": ErrorSpec("gpt_timeout", "GPT: таймаут", "Увеличь GPT_TIMEOUT_S или повтори."),
    "gpt_network": ErrorSpec("gpt_network", "GPT: сеть недоступна", "Проверь доступ к шлюзу."),
    "gpt_empty_response": ErrorSpec("gpt_empty_response", "GPT: пустой ответ", "Модель не вернула текст."),
    "gpt_bad_json": ErrorSpec("gpt_bad_json", "GPT: битый ответ", "Ответ не JSON — повтор."),
    "gpt_provider_error": ErrorSpec("gpt_provider_error", "GPT: ошибка провайдера", "См. текст ответа шлюза."),
    # ── Картинки/видео (outsee / kie) ──
    "media_no_key": ErrorSpec(
        "media_no_key",
        "Генерация: нет ключа",
        "Задай OUTSEE_API_KEY / KIE_API_KEY.",
    ),
    "media_moderation": ErrorSpec("media_moderation", "Генерация: модерация", "Промт отклонён — смягчи."),
    "media_failed": ErrorSpec("media_failed", "Генерация не удалась", "Провайдер вернул ошибку — повтор."),
    "media_timeout": ErrorSpec("media_timeout", "Генерация: таймаут", "Долгая задача — повтори."),
    "media_download": ErrorSpec("media_download", "Скачивание не удалось", "URL результата недоступен."),
    "media_empty": ErrorSpec("media_empty", "Пустой файл результата", "Провайдер вернул пустышку."),
    "media_auth": ErrorSpec("media_auth", "Генерация: ключ/доступ", "Проверь OUTSEE/KIE API ключ."),
    "media_credits": ErrorSpec("media_credits", "Генерация: нет кредитов", "Пополни баланс провайдера."),
    "media_rate_limit": ErrorSpec("media_rate_limit", "Генерация: rate limit", "Снизь параллельность, подожди."),
    "media_start_frame_policy": ErrorSpec(
        "media_start_frame_policy",
        "Генерация: кадр отклонён",
        "Смягчаем стартовый кадр и повторяем.",
    ),
    "media_audio_policy": ErrorSpec(
        "media_audio_policy",
        "Генерация: политика звука",
        "Silent + санация промта.",
    ),
    "media_prompt_length": ErrorSpec(
        "media_prompt_length",
        "Генерация: промт слишком длинный",
        "Сжимаем/режем промт под лимит модели.",
    ),
    "media_validation": ErrorSpec(
        "media_validation",
        "Генерация: неверные параметры",
        "Проверь duration/aspect/image_url.",
    ),
    "media_ladder_exhausted": ErrorSpec(
        "media_ladder_exhausted",
        "Видео: лестница исчерпана",
        "Veo+rewrite и Kling 2.6 не дали клип — кадр пропущен.",
    ),
    "media_model_fallback": ErrorSpec(
        "media_model_fallback",
        "Видео: смена модели",
        "Переход Veo → Kling 2.6.",
    ),
    # ── Файлы / xlsx ──
    "file_missing": ErrorSpec("file_missing", "Нет входного файла", "Проверь входы ноды/стрелки."),
    "xlsx_invalid": ErrorSpec("xlsx_invalid", "Некорректный xlsx", "Структура листов не совпадает."),
    "xlsx_corrupt": ErrorSpec("xlsx_corrupt", "Битый xlsx", "Файл не открывается — восстанови."),
    "download_failed": ErrorSpec("download_failed", "Ошибка скачивания", "Контент по ссылке недоступен."),
    "copy_failed": ErrorSpec("copy_failed", "Ошибка копирования", "Источник не найден."),
    # ── Проверочные ноды (vp.check.v1) ──
    "check_no_json": ErrorSpec("check_no_json", "Проверка: нет JSON", "Модель не вернула vp.check.v1."),
    "check_schema": ErrorSpec("check_schema", "Проверка: неверная схема", "Ответ не соответствует vp.check.v1."),
    # ── Пайплайн / граф ──
    "pipeline_input_not_ready": ErrorSpec(
        "pipeline_input_not_ready", "Входы не готовы", "Предыдущие ноды не завершены.",
    ),
    "pipeline_no_files_on_edge": ErrorSpec(
        "pipeline_no_files_on_edge", "Нет файлов на входе", "У ноды-источника нет результата.",
    ),
    "pipeline_node_unresolved": ErrorSpec(
        "pipeline_node_unresolved", "Нода не сопоставлена", "Для excel_gpt нужен node_key.",
    ),
    "pipeline_cancelled": ErrorSpec("pipeline_cancelled", "Остановлено пользователем", "Шаг снят через ⏹."),
    # ── Инфраструктура ──
    "infra_db_locked": ErrorSpec("infra_db_locked", "БД занята", "Параллельная запись — повтор."),
    "unknown": ErrorSpec("unknown", "Неизвестная ошибка", "См. логи data/backend.log."),
}


def _is_media_error(exc: Exception, ctx: dict) -> bool:
    """Картинка/видео (Outsee/Kling) ≠ текстовый LLM на ноде."""
    name = type(exc).__name__
    if name in {"OutseeImageError", "KieKlingError"}:
        return True
    provider = str(ctx.get("provider") or "").lower()
    return provider in {"outsee", "kie"}


def _match_code(exc: Exception) -> str:  # noqa: C901
    """Определить код ошибки по типу/тексту исключения."""
    name = type(exc).__name__
    msg = str(exc)
    low = msg.lower()

    # GptApiError / OutseeImageError несут context — используем его.
    ctx = getattr(exc, "context", None)
    if isinstance(ctx, dict):
        kind = str(ctx.get("error_kind") or "")
        status = ctx.get("status_code")
        pcode = ctx.get("provider_code")
        media = _is_media_error(exc, ctx)
        if kind == "no_key":
            return "media_no_key" if media else "gpt_no_key"
        if kind == "no_base":
            return "gpt_no_base"
        if kind == "timeout":
            return "media_timeout" if media else "gpt_timeout"
        if kind == "network":
            return "gpt_network"
        code_num = pcode if pcode is not None else status
        if code_num in (401, 403):
            return "media_auth" if media else "gpt_auth"
        if code_num == 429:
            return "media_rate_limit" if media else "gpt_rate_limit"
        if isinstance(code_num, int) and code_num >= 500:
            return "media_failed" if media else "gpt_server"

    if "apikey error" in low or "not authorized" in low:
        return "gpt_model_unauthorized"
    if "not register" in low or "not supported" in low or "model not" in low:
        return "gpt_model_unsupported"
    if "пустой choices" in low or "пустой content" in low or "пустой output" in low:
        return "gpt_empty_response"
    if "vp.check.v1" in low:
        return "check_no_json"
    if "не json" in low or "нет json" in low:
        return "gpt_bad_json"
    if name == "VideoLadderExhaustedError" or "video ladder exhausted" in low:
        return "media_ladder_exhausted"
    if "moderation" in low or "violation" in low:
        return "media_moderation"
    if "download" in low or "скач" in low:
        return "media_download"
    if "insufficient credit" in low or "нет кредит" in low:
        return "media_credits"
    if "rate limit" in low or "429" in low:
        return "media_rate_limit"
    if "database is locked" in low or "database locked" in low:
        return "infra_db_locked"
    if name in ("FileNotFoundError",) or "нет файл" in low or "не найден" in low:
        return "file_missing"
    if "xlsx-sync" in low or "xlsx" in low:
        if "corrupt" in low or "не открыв" in low:
            return "xlsx_corrupt"
        return "xlsx_invalid"
    if name in ("StepCancelledError", "CancelledError"):
        return "pipeline_cancelled"
    if "timeout" in low or "таймаут" in low:
        return "gpt_timeout"
    if "chrome" in low or "cdp" in low:
        return "unknown"
    return "unknown"


def describe_error(exc: Exception) -> tuple[str, str]:
    """(code, короткое сообщение для ноды) для любого исключения."""
    code = _match_code(exc)
    spec = ERROR_CATALOG.get(code, ERROR_CATALOG["unknown"])
    detail = str(exc).replace("\n", " ").strip()
    if code == "unknown":
        return code, (detail[:300] or spec.title)
    return code, f"{spec.title}: {detail[:200]}"


def format_node_error(exc: Exception) -> str:
    """Текст для NodeRun.error / WS payload."""
    return describe_error(exc)[1]


def all_error_codes() -> list[str]:
    return list(ERROR_CATALOG.keys())
