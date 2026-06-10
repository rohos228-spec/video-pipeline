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

# Если ChatGPT завис на сжатии — не блокировать остальные 80+ кадров.
_GPT_COMPRESS_OUTER_TIMEOUT_S = 180.0
from typing import Any

from loguru import logger

from app.bots.chatgpt import ChatGPTBot
from app.bots.outsee import (
    GenerationResult,
    OutseeBot,
    OutseeContentRejectedError,
    OutseeDownloadError,
    OutseeImageError,
)
from app.generation_options import OUTSEE_PROMPT_MAX_CHARS, prepend_gen_id
from app.services.step_cancel import abort_if_cancelled, sleep_cancellable
from app.services.step_cancel import StepCancelledError

# Meta-промт для GPT-rewrite. Точная формулировка от пользователя.
_GPT_REWRITE_META = (
    "пришли только готовый текст без твоих рассуждений, на промт ниже "
    "генератор ругается, исправь ошибки и триггеры, которые считаешь "
    "нужным и пришли только отредактированный текст"
)

# Минимальная длина «осмысленного» rewrite — отсекает «ok», «готово» и
# прочие пустышки, которые ChatGPT иногда возвращает при перегрузке.
_MIN_REWRITE_LEN = 30


def _outsee_full_prompt(body: str, prefix: str | None) -> str:
    if prefix:
        return prepend_gen_id(body, prefix)
    return body


async def _compress_prompt_for_outsee(
    gpt: ChatGPTBot,
    prompt_body: str,
    *,
    prefix: str | None = None,
    project_id: int | None = None,
) -> str | None:
    """Сжимает тело промта до лимита outsee (как hero-flow в generate_hero)."""
    reserve = (len(prefix) + 2) if prefix else 0
    max_body = max(400, OUTSEE_PROMPT_MAX_CHARS - reserve)
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
        last = (reply or "").strip()
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
) -> str:
    full = _outsee_full_prompt(prompt_body, prefix)
    if len(full) <= OUTSEE_PROMPT_MAX_CHARS:
        return prompt_body
    logger.warning(
        "outsee_retry: промт {} симв > лимита outsee {} — сжимаю через GPT",
        len(full),
        OUTSEE_PROMPT_MAX_CHARS,
    )
    if gpt is None:
        raise OutseeImageError(
            f"outsee: промт {len(full)} симв — лимит {OUTSEE_PROMPT_MAX_CHARS}, "
            "GPT недоступен для сжатия",
            context={"prompt_len": len(full), "limit": OUTSEE_PROMPT_MAX_CHARS},
        )
    compressed = await _compress_prompt_for_outsee(
        gpt, prompt_body, prefix=prefix, project_id=project_id
    )
    if not compressed:
        raise OutseeImageError(
            f"outsee: не удалось сжать промт {len(full)} симв до "
            f"{OUTSEE_PROMPT_MAX_CHARS}",
            context={"prompt_len": len(full), "limit": OUTSEE_PROMPT_MAX_CHARS},
        )
    full2 = _outsee_full_prompt(compressed, prefix)
    if len(full2) > OUTSEE_PROMPT_MAX_CHARS:
        raise OutseeImageError(
            f"outsee: после сжатия промт всё ещё {len(full2)} симв "
            f"(лимит {OUTSEE_PROMPT_MAX_CHARS})",
            context={"prompt_len": len(full2), "limit": OUTSEE_PROMPT_MAX_CHARS},
        )
    return compressed


async def _ask_gpt_to_rewrite(
    gpt: ChatGPTBot,
    original_prompt: str,
    *,
    project_id: int | None = None,
) -> str | None:
    """Запрашивает у ChatGPT переписанный промт без триггеров модерации.
    Возвращает stripped-текст, либо None если rewrite не получился."""
    full_request = (
        f"{_GPT_REWRITE_META}. Лимит outsee: ≤{OUTSEE_PROMPT_MAX_CHARS} символов.\n\n"
        f"{original_prompt}"
    )
    try:
        reply = await gpt.ask_fresh(
            full_request, timeout=600, project_id=project_id
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "outsee_retry: GPT-rewrite не получился ({}: {}) — "
            "продолжать нечем",
            type(e).__name__, e,
        )
        return None
    text = (reply or "").strip()
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
    if len(text) > OUTSEE_PROMPT_MAX_CHARS:
        logger.warning(
            "outsee_retry: GPT-rewrite {} симв > {} — попросим сжать при отправке",
            len(text), OUTSEE_PROMPT_MAX_CHARS,
        )
    return text


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
    """Обёртка над `OutseeBot.generate_image` с авто-ретраем и
    GPT-rewrite. Подробности — в docstring модуля.

    Все `kwargs` пробрасываются как есть в `outsee.generate_image`.
    На каждой попытке `prompt_id_prefix` уникализируется (см.
    `_uniquify_prompt_id`), чтобы анти-дубликат-чек outsee не
    путал retry с прошлой проваленной карточкой.
    """
    last_err: OutseeImageError | None = None
    current_prompt = prompt
    base_prompt_id = kwargs.get("prompt_id_prefix")
    rounds: list[tuple[str, str]] = [("original", current_prompt)]
    if gpt_rewrite and gpt is not None:
        rounds.append(("rewritten", ""))  # placeholder, заполним если дойдём

    for round_idx, (round_label, _) in enumerate(rounds):
        pid = kwargs.get("project_id")
        for attempt in range(1, max_attempts_per_prompt + 1):
            abort_if_cancelled(pid if isinstance(pid, int) else None)
            attempt_kwargs = dict(kwargs)
            attempt_kwargs["prompt_id_prefix"] = _uniquify_prompt_id(
                base_prompt_id, round_idx, attempt
            )
            try:
                send_prompt = await _prepare_prompt_for_outsee(
                    gpt,
                    current_prompt,
                    attempt_kwargs.get("prompt_id_prefix")
                    if isinstance(attempt_kwargs.get("prompt_id_prefix"), str)
                    else None,
                    project_id=pid if isinstance(pid, int) else None,
                )
                return await outsee.generate_image(
                    send_prompt, out_path, **attempt_kwargs
                )
            except StepCancelledError:
                raise
            except OutseeImageError as e:
                last_err = e
                err_kind = (
                    "модерация"
                    if isinstance(e, OutseeContentRejectedError)
                    else "ошибка"
                )
                logger.warning(
                    "outsee.generate_image [{}] попытка {}/{} ({}, id={}): {}",
                    round_label, attempt, max_attempts_per_prompt,
                    err_kind,
                    attempt_kwargs.get("prompt_id_prefix") or "—",
                    e.reason,
                )
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
    Логика идентична: 3 попытки → GPT-rewrite → ещё 3 попытки.

    `outsee.generate_video` бросает тот же базовый класс `OutseeImageError`
    при ошибках UI-уровня (не нашлась кнопка / таймаут), поэтому мы
    переиспользуем тот же except-handler.

    По умолчанию `uniquify_prompt_id=False`: retry ждёт ту же карточку outsee
    (не кликает Generate повторно), пока не скачает ролик или не упадёт
    окончательно. Для картинок в `generate_image_with_retries` — True.
    """
    last_err: OutseeImageError | None = None
    current_prompt = prompt
    base_prompt_id = kwargs.get("prompt_id_prefix")
    rounds: list[str] = ["original"]
    if gpt_rewrite and gpt is not None:
        rounds.append("rewritten")

    _DOWNLOAD_ONLY_RETRIES = 2

    for round_idx, round_label in enumerate(rounds):
        for attempt in range(1, max_attempts_per_prompt + 1):
            abort_if_cancelled(project_id)
            attempt_kwargs = dict(kwargs)
            if uniquify_prompt_id:
                attempt_kwargs["prompt_id_prefix"] = _uniquify_prompt_id(
                    base_prompt_id, round_idx, attempt
                )
            else:
                attempt_kwargs["prompt_id_prefix"] = base_prompt_id
            try:
                return await outsee.generate_video(
                    current_prompt, out_path, project_id=project_id,
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
                        "исчерпаны (id={})",
                        round_label,
                        attempt_kwargs.get("prompt_id_prefix") or "—",
                    )
                last_err = e
                if attempt < max_attempts_per_prompt:
                    await sleep_cancellable(2.0, project_id)
            except OutseeImageError as e:
                last_err = e
                err_kind = (
                    "модерация"
                    if isinstance(e, OutseeContentRejectedError)
                    else "ошибка"
                )
                logger.warning(
                    "outsee.generate_video [{}] попытка {}/{} ({}, id={}): {}",
                    round_label, attempt, max_attempts_per_prompt,
                    err_kind,
                    attempt_kwargs.get("prompt_id_prefix") or "—",
                    e.reason,
                )
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
            gpt, current_prompt, project_id=project_id
        )
        if not rewritten:
            logger.warning(
                "outsee.generate_video: GPT-rewrite не вернул текст — выхожу"
            )
            break
        current_prompt = rewritten

    if last_err is None:
        raise RuntimeError("generate_video_with_retries: unreachable")
    raise last_err
