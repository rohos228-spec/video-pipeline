"""Утилита-обёртка вокруг `OutseeBot.generate_image` и `.generate_video`,
которая:

  1) Повторяет попытку генерации до `MAX_ATTEMPTS_PER_PROMPT` раз
     (по умолчанию 3) на любой `OutseeImageError` / `OutseeVideoError`,
     включая модерационную ошибку «Контент отклонён»
     (`OutseeContentRejectedError`). Между попытками — пауза 2 сек.

  2) Если все 3 попытки провалились, опционально просит ChatGPT
     переписать промт без триггеров модерации, отправляя в новый чат
     ровно тот meta-промт, который определил пользователь:

       «пришли только готовый текст без твоих рассуждений, на промт
        ниже генератор ругается, исправь ошибки и триггеры, которые
        считаешь нужным и пришли только отредактированный текст
        \n\n<сам_промт>»

     После этого делает ещё одну серию из 3 попыток уже с переписанным
     промтом. Если и она провалилась — пробрасывает последнюю ошибку
     из второй серии (caller сам решит, что делать).

  Видео (`generate_video_with_retries`): после 3 сбоев генерации переключает
  outsee на Kling 2.5 Turbo, 720p и соотношение «Исходное»; дальнейшие
  попытки (включая раунд GPT-rewrite) идут уже с этими параметрами.

  3) Если `gpt=None` или GPT-rewrite сам упал — caller получит
     последнюю ошибку первой серии.

Использование:
    result = await generate_image_with_retries(
        outsee, gpt,
        prompt=prompt_text,
        out_path=out_path,
        max_attempts_per_prompt=3,
        gpt_rewrite=True,
        ...kwargs,  # все аргументы outsee.generate_image
    )

Caller'ы:
  - app/orchestrator/steps/generate_hero.py
  - app/orchestrator/steps/generate_images.py
  - app/orchestrator/steps/generate_videos.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

# Должен быть ≥ timeout в gpt.ask_fresh, иначе сжатие обрывается раньше ответа.
_GPT_COMPRESS_OUTER_TIMEOUT_S = 620.0
_GPT_REWRITE_OUTER_TIMEOUT_S = 620.0
# Модерация: очередь картинок последовательная — длинный GPT = «всё встало».
# Один rewrite с коротким таймаутом; overrun режем локально, без GPT-сжатия.
_GPT_MODERATION_REWRITE_OUTER_TIMEOUT_S = 180.0
_GPT_MODERATION_REWRITE_ASK_TIMEOUT_S = 150.0
# Хвост uniquify `[… r9a9]` — резерв при расчёте max_body.
_UNIQUIFY_SUFFIX_RESERVE = 8
from typing import Any

from loguru import logger

from app.bots.chatgpt import ChatGPTBot
from app.bots.outsee import (
    GenerationResult,
    OutseeBot,
    OutseeContentRejectedError,
    OutseeDownloadError,
    OutseeImageError,
    OutseePromptTooLongError,
    outsee_error_is_moderation,
    outsee_error_kind,
    outsee_error_kind_label,
)
from app.generation_options import (
    OUTSEE_PROMPT_MAX_CHARS,
    OUTSEE_VIDEO_FALLBACK_AFTER_FAILURES,
    outsee_video_fallback_fields,
    prepend_gen_id,
    strip_prompt_id_lines,
)
from app.services.step_cancel import abort_if_cancelled, sleep_cancellable
from app.services.step_cancel import StepCancelledError

def _gpt_moderation_rewrite_meta(body_limit: int) -> str:
    """Meta для GPT-rewrite при модерации — лимит = тело без [ID:], не full."""
    return (
        "измени промт ниже, но сохрани смысл картины и деталей, "
        f"промт не должен быть больше {body_limit} символов "
        "(это лимит тела без строки [ID: …]), "
        "замени опасные, триггерные слова на синонимы более нейтральные "
        "и пришли только текст промта в ответе."
    )


def _hard_truncate_prompt(text: str, body_limit: int) -> str:
    """Обрезка тела промта по лимиту (по пробелу, если есть)."""
    if len(text) <= body_limit:
        return text
    cut = text[:body_limit]
    sp = cut.rfind(" ")
    if sp > int(body_limit * 0.85):
        cut = cut[:sp]
    return cut

# Fallback для rewrite не из-за модерации (редко — второй раунд после других сбоев).
_GPT_REWRITE_META = (
    "пришли только готовый текст без рассуждений: исправь промт ниже "
    "и пришли только отредактированный текст."
)

# Минимальная длина «осмысленного» rewrite — отсекает «ok», «готово» и
# прочие пустышки, которые ChatGPT иногда возвращает при перегрузке.
_MIN_REWRITE_LEN = 30


def _outsee_full_prompt(body: str, prefix: str | None) -> str:
    if prefix:
        return prepend_gen_id(body, prefix)
    return body


def _prefix_reserve(prefix: str | None) -> int:
    """Запас под `[ID: …]` + `\n\n` + хвост uniquify ` r9a9]`."""
    if not prefix:
        return _UNIQUIFY_SUFFIX_RESERVE
    return len(prefix) + 2 + _UNIQUIFY_SUFFIX_RESERVE


def _max_body_for_prefix(prefix: str | None, *, cap: int | None = None) -> int:
    limit = cap if cap is not None else OUTSEE_PROMPT_MAX_CHARS
    return max(400, limit - _prefix_reserve(prefix))


_PROMPT_ERROR_MARKERS = (
    "промт обрезан",
    "лимит outsee",
    "не попал в поле",
    "id промта не найден",
    "не удалось сжать",
    "gpt недоступен для сжатия",
)


def _is_prompt_related_error(err: OutseeImageError) -> bool:
    """Ошибки длины/обрезки промта — нужно GPT-сжатие, не rewrite модерации."""
    if outsee_error_is_moderation(err):
        return False
    if isinstance(err, OutseePromptTooLongError):
        return True
    if isinstance(err, OutseeContentRejectedError):
        return False
    reason = (err.reason or "").lower()
    if any(m in reason for m in _PROMPT_ERROR_MARKERS):
        return True
    ctx = err.context or {}
    if ctx.get("actual_len") is not None and ctx.get("expected_len") is not None:
        try:
            actual = int(ctx["actual_len"])
            expected = int(ctx["expected_len"])
        except (TypeError, ValueError):
            return False
        return expected >= 200 and actual < int(expected * 0.85)
    return False


def _target_body_chars_from_error(
    err: OutseeImageError,
    prefix: str | None,
) -> int | None:
    """Целевая длина тела промта после обрезки outsee (если известна)."""
    ctx = err.context or {}
    actual = ctx.get("actual_len")
    if actual is not None:
        try:
            actual_i = int(actual)
        except (TypeError, ValueError):
            actual_i = 0
        if actual_i >= 200:
            # ID-prefix уже в textarea — вычитаем его из фактической длины.
            reserve = _prefix_reserve(prefix)
            return max(400, actual_i - reserve - 20)
    prompt_len = ctx.get("prompt_len")
    if prompt_len is not None:
        try:
            pl = int(prompt_len)
        except (TypeError, ValueError):
            pl = 0
        if pl > OUTSEE_PROMPT_MAX_CHARS:
            return _max_body_for_prefix(prefix) - 200
    return None


async def _compress_prompt_for_outsee(
    gpt: ChatGPTBot,
    prompt_body: str,
    *,
    prefix: str | None = None,
    project_id: int | None = None,
    max_body: int | None = None,
) -> str | None:
    """Сжимает тело промта до лимита outsee (как hero-flow в generate_hero)."""
    max_body = max_body if max_body is not None else _max_body_for_prefix(prefix)
    last = prompt_body.strip()
    if len(last) <= max_body:
        return last
    meta = (
        f"Сожми промт для outsee.io до ≤{max_body} символов (включая пробелы). "
        "Убери повторы и воду, оставь суть и визуальные детали. "
        "Верни ТОЛЬКО новый текст без пояснений."
    )
    for attempt in range(1, 4):
        if attempt == 1:
            ask = f"{meta}\n\n{last}"
        else:
            ask = (
                f"Прошлый ответ был {len(last)} символов — нужно ≤{max_body}. "
                f"Сожми ещё сильнее, сохрани суть. Верни ТОЛЬКО текст.\n\n"
                f"Прошлый промт:\n\n{last}"
            )
        logger.info(
            "outsee_retry: GPT-сжатие attempt {}/{} — жду ответ ChatGPT "
            "(промт {} симв, лимит {})",
            attempt,
            3,
            len(ask),
            max_body,
        )
        try:
            reply = await asyncio.wait_for(
                gpt.ask_fresh(ask, timeout=600, project_id=project_id),
                timeout=_GPT_COMPRESS_OUTER_TIMEOUT_S,
            )
        except asyncio.TimeoutError:
            logger.error(
                "outsee_retry: GPT-сжатие таймаут {:.0f}с — кадр failed, "
                "воркер идёт к следующему",
                _GPT_COMPRESS_OUTER_TIMEOUT_S,
            )
            return None
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "outsee_retry: GPT-сжатие упало ({}: {})", type(e).__name__, e
            )
            return None
        last = strip_prompt_id_lines((reply or "").strip())
        if len(last) < _MIN_REWRITE_LEN:
            continue
        if len(last) <= max_body:
            logger.info(
                "outsee_retry: GPT-сжатие OK: {} → {} симв (лимит {})",
                len(prompt_body), len(last), max_body,
            )
            return last
        logger.warning(
            "outsee_retry: GPT-сжатие attempt {}: {} симв (нужно ≤{})",
            attempt, len(last), max_body,
        )
    return None


async def _prepare_prompt_for_outsee(
    gpt: ChatGPTBot | None,
    prompt_body: str,
    prefix: str | None,
    *,
    project_id: int | None = None,
    max_body: int | None = None,
) -> str:
    prompt_body = strip_prompt_id_lines(prompt_body)
    full = _outsee_full_prompt(prompt_body, prefix)
    body_limit = max_body if max_body is not None else _max_body_for_prefix(prefix)
    if len(prompt_body) <= body_limit and len(full) <= OUTSEE_PROMPT_MAX_CHARS:
        return prompt_body
    logger.warning(
        "outsee_retry: промт {} симв (full {}), лимит body {} / outsee {} — "
        "сжимаю через GPT",
        len(prompt_body),
        len(full),
        body_limit,
        OUTSEE_PROMPT_MAX_CHARS,
    )
    if gpt is None:
        raise OutseePromptTooLongError(
            f"outsee: промт {len(full)} симв — лимит {OUTSEE_PROMPT_MAX_CHARS}, "
            "GPT недоступен для сжатия",
            context={
                "prompt_len": len(full),
                "limit": OUTSEE_PROMPT_MAX_CHARS,
                "error_kind": "length",
            },
        )
    compressed = await _compress_prompt_for_outsee(
        gpt,
        prompt_body,
        prefix=prefix,
        project_id=project_id,
        max_body=body_limit,
    )
    if not compressed:
        raise OutseePromptTooLongError(
            f"outsee: не удалось сжать промт {len(full)} симв до "
            f"{OUTSEE_PROMPT_MAX_CHARS}",
            context={
                "prompt_len": len(full),
                "limit": OUTSEE_PROMPT_MAX_CHARS,
                "error_kind": "length",
            },
        )
    full2 = _outsee_full_prompt(compressed, prefix)
    if len(full2) > OUTSEE_PROMPT_MAX_CHARS:
        raise OutseePromptTooLongError(
            f"outsee: после сжатия промт всё ещё {len(full2)} симв "
            f"(лимит {OUTSEE_PROMPT_MAX_CHARS})",
            context={
                "prompt_len": len(full2),
                "limit": OUTSEE_PROMPT_MAX_CHARS,
                "error_kind": "length",
            },
        )
    return compressed


async def _ask_gpt_to_rewrite(
    gpt: ChatGPTBot,
    original_prompt: str,
    *,
    project_id: int | None = None,
    last_error: OutseeImageError | None = None,
    prefix: str | None = None,
) -> str | None:
    """Запрашивает у ChatGPT переписанный промт без триггеров модерации.
    Возвращает stripped-текст, либо None если rewrite не получился."""
    body_limit = _max_body_for_prefix(prefix)
    moderation = last_error is not None and outsee_error_is_moderation(last_error)
    if moderation:
        full_request = (
            f"{_gpt_moderation_rewrite_meta(body_limit)}\n\n{original_prompt}"
        )
    else:
        err_hint = ""
        if last_error is not None:
            err_hint = (
                f"\n\nПоследняя ошибка outsee:\n{last_error.reason[:500]}"
            )
            if _is_prompt_related_error(last_error):
                err_hint += (
                    f"\n\nOutsee не принимает такую длину — сожми до ≤{body_limit} "
                    "символов в теле промта (без ID-строки)."
                )
        full_request = (
            f"{_GPT_REWRITE_META} Лимит outsee: ≤{body_limit} символов в теле "
            f"промта (без строки [ID: …]).\n\n"
            f"{original_prompt}{err_hint}"
        )
    ask_timeout = (
        _GPT_MODERATION_REWRITE_ASK_TIMEOUT_S if moderation else 600.0
    )
    outer_timeout = (
        _GPT_MODERATION_REWRITE_OUTER_TIMEOUT_S
        if moderation
        else _GPT_REWRITE_OUTER_TIMEOUT_S
    )
    try:
        reply = await asyncio.wait_for(
            gpt.ask_fresh(
                full_request, timeout=ask_timeout, project_id=project_id
            ),
            timeout=outer_timeout,
        )
    except asyncio.TimeoutError:
        logger.error(
            "outsee_retry: GPT-rewrite таймаут {:.0f}с{} — кадр дальше "
            "не держим, очередь картинок продолжит",
            outer_timeout,
            " (модерация)" if moderation else "",
        )
        return None
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "outsee_retry: GPT-rewrite не получился ({}: {}) — "
            "продолжать нечем",
            type(e).__name__, e,
        )
        return None
    text = strip_prompt_id_lines((reply or "").strip())
    if len(text) < _MIN_REWRITE_LEN:
        logger.warning(
            "outsee_retry: GPT-rewrite вернул слишком короткий ответ "
            "({} симв) — игнорирую",
            len(text),
        )
        return None
    logger.info(
        "outsee_retry: GPT-rewrite OK, новый промт {} симв (был {})",
        len(text), len(original_prompt),
    )
    if len(text) > body_limit:
        # После модерации НЕ зовём GPT-сжатие: очередь img последовательная,
        # сжатие = минуты без единой генерации (как в логе P47-F43).
        if moderation:
            cut = _hard_truncate_prompt(text, body_limit)
            logger.warning(
                "outsee_retry: GPT-rewrite {} симв > лимит {} — "
                "hard-truncate до {} (без GPT-сжатия, очередь не блокируем)",
                len(text),
                body_limit,
                len(cut),
            )
            return cut
        over_full = len(_outsee_full_prompt(text, prefix)) > OUTSEE_PROMPT_MAX_CHARS
        overrun = len(text) - body_limit
        if overrun <= max(120, body_limit // 20) or over_full:
            if overrun <= max(120, body_limit // 20):
                cut = _hard_truncate_prompt(text, body_limit)
                logger.warning(
                    "outsee_retry: GPT-rewrite {} симв > лимит {} "
                    "(+{}) — hard-truncate до {} симв",
                    len(text),
                    body_limit,
                    overrun,
                    len(cut),
                )
                return cut
            logger.warning(
                "outsee_retry: GPT-rewrite {} симв > лимит (body {}, full {}) — сожму",
                len(text),
                body_limit,
                len(_outsee_full_prompt(text, prefix)),
            )
            compressed = await _compress_prompt_for_outsee(
                gpt, text, prefix=prefix, project_id=project_id, max_body=body_limit
            )
            if compressed:
                text = compressed
            elif len(text) > body_limit:
                cut = _hard_truncate_prompt(text, body_limit)
                logger.warning(
                    "outsee_retry: GPT-сжатие не удалось — hard-truncate "
                    "{} → {} симв",
                    len(text),
                    len(cut),
                )
                text = cut
    return text


async def _fix_prompt_after_outsee_error(
    gpt: ChatGPTBot,
    prompt_body: str,
    err: OutseeImageError,
    *,
    prefix: str | None,
    project_id: int | None,
) -> str | None:
    """Сразу после ошибки — сжать (длина) или переписать (модерация)."""
    if outsee_error_is_moderation(err):
        logger.info(
            "outsee_retry: модерация outsee — GPT-rewrite промта ({} симв)",
            len(prompt_body),
        )
        return await _ask_gpt_to_rewrite(
            gpt,
            prompt_body,
            project_id=project_id,
            last_error=err,
            prefix=prefix,
        )
    if isinstance(err, OutseePromptTooLongError) or _is_prompt_related_error(err):
        target = _target_body_chars_from_error(err, prefix)
        body_limit = target if target is not None else _max_body_for_prefix(prefix)
        logger.info(
            "outsee_retry: лимит символов outsee после «{}» — "
            "GPT-сжатие до ≤{} симв",
            err.reason[:80],
            body_limit,
        )
        return await _compress_prompt_for_outsee(
            gpt,
            prompt_body,
            prefix=prefix,
            project_id=project_id,
            max_body=body_limit,
        )
    return None


def _retry_err_label(e: OutseeImageError) -> str:
    return outsee_error_kind_label(outsee_error_kind(e))


def _apply_video_fallback_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Kling 2.5 / 720p / «Исходное» поверх настроек проекта."""
    merged = dict(kwargs)
    merged.update(outsee_video_fallback_fields())
    return merged


def _should_use_video_fallback(*, round_idx: int, gen_failures: int) -> bool:
    """После исчерпания раунда «original» (3 попытки) — всегда fallback.

    Дополнительно: если счётчик сбоев дошёл до порога внутри раунда — тоже
    fallback (на случай max_attempts_per_prompt > 3).
    """
    if round_idx > 0:
        return True
    return gen_failures >= OUTSEE_VIDEO_FALLBACK_AFTER_FAILURES


def _video_fallback_active(kwargs: dict[str, Any]) -> bool:
    fb = outsee_video_fallback_fields()
    return (
        str(kwargs.get("model_slug") or "") == fb["model_slug"]
        and str(kwargs.get("resolution") or "") == fb["resolution"]
        and str(kwargs.get("aspect_ratio") or "") == fb["aspect_ratio"]
    )


def _uniquify_prompt_id(base: str | None, round_idx: int, attempt: int) -> str | None:
    """Делает `prompt_id_prefix` уникальным для текущей retry-итерации.

    Без этого ВСЕ попытки повторной генерации одной и той же картинки
    приходят в outsee с одинаковым ID (`[ID: P11-EXCEL-c01]`) — и
    анти-дубликат-чек в `_generate_image_on_page` находит ОСТАВШУЮСЯ
    карточку прошлой провалившейся попытки, решает «генерация уже идёт,
    не кликаю Generate повторно» и пытается скачать ту же отбракованную
    модерацией картинку. Из-за этого retry превращался в три «не делай
    ничего» на одной и той же сломанной карточке.

    Меняем хвост ID на `[… r{round}a{attempt}]` — каждая новая попытка
    становится отдельной картой outsee со своим ID, и анти-дуп-чек
    больше не путает её с прошлыми.
    """
    if not base:
        return base
    if round_idx == 0 and attempt == 1:
        return base  # первая попытка — оригинальный ID без шума
    # base = "[ID: P11-EXCEL-c01]" → "[ID: P11-EXCEL-c01 r1a2]"
    stripped = base.strip()
    if stripped.endswith("]"):
        return f"{stripped[:-1]} r{round_idx + 1}a{attempt}]"
    return f"{stripped} r{round_idx + 1}a{attempt}"


async def generate_image_with_retries(
    outsee: OutseeBot,
    gpt: ChatGPTBot | None,
    *,
    prompt: str,
    out_path: Path,
    max_attempts_per_prompt: int = 3,
    gpt_rewrite: bool = True,
    **kwargs: Any,
) -> GenerationResult:
    """Обёртка над `OutseeBot.generate_image` / Grsai с авто-ретраем и
    GPT-rewrite. Подробности — в docstring модуля.

    Все `kwargs` пробрасываются как есть в `outsee.generate_image`
    (или в `grsai.generate_image`, если IMAGE_PROVIDER=grsai).
    `prompt_id_prefix` один на весь кадр (все retry и GPT-rewrite) —
    формат `[ID: P12-F3-a7f2b01c]`, где `a7f2b01c` = gen_id этой генерации.
    """
    from app.bots.grsai import grsai_enabled, generate_image as grsai_generate_image
    from app.bots.grsai import studio_id_to_grsai_slug
    from app.settings import settings as _settings

    use_grsai = grsai_enabled()
    last_err: OutseeImageError | None = None
    current_prompt = prompt
    base_prompt_id = kwargs.get("prompt_id_prefix")
    rounds: list[tuple[str, str]] = [("original", current_prompt)]
    if gpt_rewrite and gpt is not None:
        rounds.append(("rewritten", ""))  # placeholder, заполним если дойдём
    _DOWNLOAD_ONLY_RETRIES = 2

    for round_idx, (round_label, _) in enumerate(rounds):
        pid = kwargs.get("project_id")
        for attempt in range(1, max_attempts_per_prompt + 1):
            abort_if_cancelled(pid if isinstance(pid, int) else None)
            attempt_kwargs = dict(kwargs)
            attempt_kwargs["prompt_id_prefix"] = base_prompt_id
            try:
                send_prompt = await _prepare_prompt_for_outsee(
                    gpt,
                    current_prompt,
                    attempt_kwargs.get("prompt_id_prefix")
                    if isinstance(attempt_kwargs.get("prompt_id_prefix"), str)
                    else None,
                    project_id=pid if isinstance(pid, int) else None,
                )
                if use_grsai:
                    from app.bots.grsai import GRSAI_WIRED_IMAGE_MODELS

                    raw_slug = attempt_kwargs.get("model_slug") or getattr(
                        _settings, "grsai_default_image_model", None
                    )
                    slug = studio_id_to_grsai_slug(
                        str(raw_slug) if raw_slug else None
                    )
                    if slug not in GRSAI_WIRED_IMAGE_MODELS:
                        slug = studio_id_to_grsai_slug(
                            getattr(_settings, "grsai_default_image_model", None)
                        )
                    ar = attempt_kwargs.get("aspect_ratio") or "9:16"
                    res = attempt_kwargs.get("resolution") or attempt_kwargs.get(
                        "image_resolution"
                    )
                    result = await grsai_generate_image(
                        send_prompt,
                        out_path,
                        model_slug=slug,
                        aspect_ratio=str(ar).replace("_", ":"),
                        resolution=str(res) if res else "1K",
                        reference_image=attempt_kwargs.get("reference_image"),
                        timeout=float(attempt_kwargs.get("timeout") or 600),
                        gen_id=attempt_kwargs.get("gen_id"),
                        project_id=pid if isinstance(pid, int) else None,
                    )
                    # Метаданные рядом с файлом на диске (папка проекта уже создана)
                    try:
                        from app.services.generation_storage import write_sidecar
                        from app.services.grsai_pricing import quote_generation

                        write_sidecar(
                            result.file_path,
                            media="image",
                            model=slug,
                            prompt=send_prompt,
                            params={
                                "aspect": str(ar).replace("_", ":"),
                                "resolution": str(res) if res else "1K",
                                "project_id": pid,
                                "gen_id": attempt_kwargs.get("gen_id"),
                            },
                            raw_url=result.raw_url,
                            quote=quote_generation(
                                media="image",
                                model=slug,
                                resolution=str(res) if res else "1K",
                            ),
                            provider="grsai",
                        )
                    except Exception:  # noqa: BLE001
                        logger.debug("grsai sidecar write skipped", exc_info=True)
                    return result
                return await outsee.generate_image(
                    send_prompt, out_path, **attempt_kwargs
                )
            except StepCancelledError:
                raise
            except OutseeDownloadError as e:
                # Generate уже прошёл — только повтор скачивания, без нового Generate.
                raw_url = e.context.get("img_url")
                gen_id = str(e.context.get("gen_id") or "")
                prefix = attempt_kwargs.get("prompt_id_prefix")
                from app.bots.outsee import _resolve_best_download_url

                img_url = (
                    _resolve_best_download_url(raw_url)
                    if isinstance(raw_url, str) and raw_url
                    else raw_url
                )
                if isinstance(img_url, str) and img_url and gen_id:
                    for dl_try in range(1, _DOWNLOAD_ONLY_RETRIES + 1):
                        abort_if_cancelled(
                            pid if isinstance(pid, int) else None
                        )
                        try:
                            return await outsee.retry_image_download(
                                img_url=img_url,
                                out_path=out_path,
                                gen_id=gen_id,
                                prompt_id_prefix=(
                                    prefix if isinstance(prefix, str) else None
                                ),
                                project_id=pid if isinstance(pid, int) else None,
                                model_slug=attempt_kwargs.get("model_slug"),
                            )
                        except OutseeDownloadError as dl_err:
                            last_err = dl_err
                            logger.warning(
                                "outsee.retry_image_download [{}] {}/{}: {}",
                                round_label,
                                dl_try,
                                _DOWNLOAD_ONLY_RETRIES,
                                dl_err.reason,
                            )
                            if dl_try < _DOWNLOAD_ONLY_RETRIES:
                                await sleep_cancellable(
                                    2.0,
                                    pid if isinstance(pid, int) else None,
                                )
                    logger.warning(
                        "outsee.generate_image [{}] download-only retries "
                        "исчерпаны (id={}) — без нового Generate",
                        round_label,
                        prefix or "—",
                    )
                # Карточка уже на outsee: повторный Generate только orphan'ит результат.
                raise last_err or e
            except OutseeImageError as e:
                last_err = e
                err_kind = _retry_err_label(e)
                is_moderation = outsee_error_is_moderation(e)
                if is_moderation:
                    logger.warning(
                        "outsee.generate_image [{}] МОДЕРАЦИЯ outsee "
                        "попытка {}/{} (id={}): {}",
                        round_label,
                        attempt,
                        max_attempts_per_prompt,
                        attempt_kwargs.get("prompt_id_prefix") or "—",
                        e.reason,
                    )
                else:
                    logger.warning(
                        "outsee.generate_image [{}] попытка {}/{} ({}, id={}): {}",
                        round_label, attempt, max_attempts_per_prompt,
                        err_kind,
                        attempt_kwargs.get("prompt_id_prefix") or "—",
                        e.reason,
                    )
                prefix = (
                    attempt_kwargs.get("prompt_id_prefix")
                    if isinstance(attempt_kwargs.get("prompt_id_prefix"), str)
                    else None
                )
                moderation_rewrite_failed = False
                if (
                    gpt is not None
                    and attempt < max_attempts_per_prompt
                    and (
                        isinstance(e, OutseePromptTooLongError)
                        or _is_prompt_related_error(e)
                        or is_moderation
                    )
                ):
                    fixed = await _fix_prompt_after_outsee_error(
                        gpt,
                        current_prompt,
                        e,
                        prefix=prefix,
                        project_id=pid if isinstance(pid, int) else None,
                    )
                    if fixed and fixed.strip() != current_prompt.strip():
                        action = (
                            "GPT-rewrite" if is_moderation else "GPT-сжатие"
                        )
                        logger.info(
                            "outsee.generate_image [{}]: {} OK "
                            "({} → {} симв, ошибка={})",
                            round_label,
                            action,
                            len(current_prompt),
                            len(fixed),
                            err_kind,
                        )
                        current_prompt = fixed
                    elif is_moderation:
                        moderation_rewrite_failed = True
                        logger.warning(
                            "outsee.generate_image [{}]: модерация — GPT не "
                            "переписал промт, не повторяю тот же текст",
                            round_label,
                        )
                elif is_moderation and gpt is None:
                    # Без GPT бессмысленно долбить тот же banned-промт 3×.
                    logger.warning(
                        "outsee.generate_image [{}]: модерация без GPT — "
                        "не повторяю тот же текст",
                        round_label,
                    )
                    break
                if moderation_rewrite_failed:
                    break
                if attempt < max_attempts_per_prompt:
                    await sleep_cancellable(2.0, pid if isinstance(pid, int) else None)

        # Все попытки в этом раунде провалились. Если ещё есть раунд
        # «rewritten» — попробуем переписать промт через GPT.
        is_last_round = round_idx == len(rounds) - 1
        if is_last_round:
            break
        if gpt is None:
            logger.warning(
                "outsee.generate_image [{}]: GPT недоступен — rewrite пропущен",
                round_label,
            )
            break
        logger.info(
            "outsee.generate_image: GPT-rewrite после раунда «{}»",
            round_label,
        )
        rewritten = await _ask_gpt_to_rewrite(
            gpt,
            current_prompt,
            project_id=pid if isinstance(pid, int) else None,
            last_error=last_err,
            prefix=base_prompt_id,
        )
        if not rewritten:
            logger.warning(
                "outsee.generate_image: GPT-rewrite не вернул текст — выхожу"
            )
            break
        current_prompt = rewritten

    if last_err is None:
        # сюда мы попасть не должны (raise/return должны были отработать)
        raise RuntimeError("generate_image_with_retries: unreachable")
    raise last_err


async def generate_video_with_retries(
    outsee: OutseeBot,
    gpt: ChatGPTBot | None,
    *,
    prompt: str,
    out_path: Path,
    max_attempts_per_prompt: int = 3,
    gpt_rewrite: bool = True,
    project_id: int | None = None,
    uniquify_prompt_id: bool = False,
    **kwargs: Any,
) -> GenerationResult:
    """Аналог `generate_image_with_retries` для видео-генерации.

    1) До 3 попыток с моделью проекта (раунд «original»).
    2) Со 2-го раунда (GPT-rewrite / fallback) — Kling 2.5 Turbo, 720p,
       соотношение «Исходное» (по стартовому кадру).
    3) Если в одном раунде >3 попыток — fallback также после 3 сбоев.

    `outsee.generate_video` бросает тот же базовый класс `OutseeImageError`
    при ошибках UI-уровня (не нашлась кнопка / таймаут), поэтому мы
    переиспользуем тот же except-handler.
    """
    last_err: OutseeImageError | None = None
    current_prompt = prompt
    base_prompt_id = kwargs.get("prompt_id_prefix")
    rounds: list[str] = ["original"]
    if gpt_rewrite and gpt is not None:
        rounds.append("rewritten")
    else:
        rounds.append("fallback_model")

    _DOWNLOAD_ONLY_RETRIES = 2
    gen_failures = 0
    fallback_logged = False

    for round_idx, round_label in enumerate(rounds):
        for attempt in range(1, max_attempts_per_prompt + 1):
            abort_if_cancelled(project_id)
            use_fallback = _should_use_video_fallback(
                round_idx=round_idx, gen_failures=gen_failures
            )
            attempt_kwargs = (
                _apply_video_fallback_kwargs(kwargs)
                if use_fallback
                else dict(kwargs)
            )
            if use_fallback and not fallback_logged:
                fb = outsee_video_fallback_fields()
                logger.info(
                    "outsee_retry: fallback video (round={}, failures={}): "
                    "model={} resolution={} aspect={}",
                    round_label,
                    gen_failures,
                    fb["model_slug"],
                    fb["resolution"],
                    fb["aspect_ratio"],
                )
                fallback_logged = True
            logger.info(
                "outsee_retry: video [{}] попытка {}/{} model={} res={} aspect={}",
                round_label,
                attempt,
                max_attempts_per_prompt,
                attempt_kwargs.get("model_slug"),
                attempt_kwargs.get("resolution"),
                attempt_kwargs.get("aspect_ratio"),
            )
            if uniquify_prompt_id:
                attempt_kwargs["prompt_id_prefix"] = _uniquify_prompt_id(
                    base_prompt_id, round_idx, attempt
                )
            else:
                attempt_kwargs["prompt_id_prefix"] = base_prompt_id
            try:
                prefix = (
                    attempt_kwargs.get("prompt_id_prefix")
                    if isinstance(attempt_kwargs.get("prompt_id_prefix"), str)
                    else None
                )
                send_prompt = await _prepare_prompt_for_outsee(
                    gpt,
                    current_prompt,
                    prefix,
                    project_id=project_id,
                )
                return await outsee.generate_video(
                    send_prompt, out_path, project_id=project_id,
                    **attempt_kwargs,
                )
            except StepCancelledError:
                raise
            except OutseeDownloadError as e:
                video_url = e.context.get("video_url")
                gen_id = str(e.context.get("gen_id") or "")
                if isinstance(video_url, str) and video_url and gen_id:
                    for dl_try in range(1, _DOWNLOAD_ONLY_RETRIES + 1):
                        abort_if_cancelled(project_id)
                        try:
                            return await outsee.retry_video_download(
                                video_url=video_url,
                                out_path=out_path,
                                gen_id=gen_id,
                                prompt_id_prefix=attempt_kwargs.get(
                                    "prompt_id_prefix"
                                ),
                                project_id=project_id,
                                model_slug=attempt_kwargs.get("model_slug"),
                            )
                        except OutseeDownloadError as dl_err:
                            last_err = dl_err
                            logger.warning(
                                "outsee.retry_video_download [{}] {}/{}: {}",
                                round_label,
                                dl_try,
                                _DOWNLOAD_ONLY_RETRIES,
                                dl_err.reason,
                            )
                            if dl_try < _DOWNLOAD_ONLY_RETRIES:
                                await sleep_cancellable(2.0, project_id)
                    logger.warning(
                        "outsee.generate_video [{}] download-only retries "
                        "исчерпаны (id={}) — без нового Generate",
                        round_label,
                        attempt_kwargs.get("prompt_id_prefix") or "—",
                    )
                # Ролик уже на outsee — не кликаем Generate снова.
                raise last_err or e
            except OutseeImageError as e:
                last_err = e
                gen_failures += 1
                err_kind = _retry_err_label(e)
                logger.warning(
                    "outsee.generate_video [{}] попытка {}/{} ({}, id={}): {}",
                    round_label, attempt, max_attempts_per_prompt,
                    err_kind,
                    attempt_kwargs.get("prompt_id_prefix") or "—",
                    e.reason,
                )
                prefix = (
                    attempt_kwargs.get("prompt_id_prefix")
                    if isinstance(attempt_kwargs.get("prompt_id_prefix"), str)
                    else None
                )
                if (
                    gpt is not None
                    and attempt < max_attempts_per_prompt
                    and (
                        isinstance(e, OutseePromptTooLongError)
                        or _is_prompt_related_error(e)
                        or isinstance(e, OutseeContentRejectedError)
                    )
                ):
                    fixed = await _fix_prompt_after_outsee_error(
                        gpt,
                        current_prompt,
                        e,
                        prefix=prefix,
                        project_id=project_id,
                    )
                    if fixed and fixed.strip() != current_prompt.strip():
                        logger.info(
                            "outsee.generate_video [{}]: prompt-fix OK "
                            "({} → {} симв)",
                            round_label,
                            len(current_prompt),
                            len(fixed),
                        )
                        current_prompt = fixed
                if attempt < max_attempts_per_prompt:
                    await sleep_cancellable(2.0, project_id)

        is_last_round = round_idx == len(rounds) - 1
        if is_last_round:
            break
        if gpt is None:
            logger.warning(
                "outsee.generate_video [{}]: GPT недоступен — rewrite пропущен",
                round_label,
            )
            break
        logger.info(
            "outsee.generate_video: GPT-rewrite после раунда «{}»",
            round_label,
        )
        rewritten = await _ask_gpt_to_rewrite(
            gpt,
            current_prompt,
            project_id=project_id,
            last_error=last_err,
            prefix=base_prompt_id if isinstance(base_prompt_id, str) else None,
        )
        if not rewritten:
            logger.warning(
                "outsee.generate_video: GPT-rewrite не вернул текст — "
                "продолжаю с исходным промтом"
            )
        else:
            current_prompt = rewritten

    if last_err is None:
        raise RuntimeError("generate_video_with_retries: unreachable")
    raise last_err
