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
from typing import Any

from loguru import logger

from app.bots.chatgpt import ChatGPTBot
from app.bots.outsee import (
    GenerationResult,
    OutseeBot,
    OutseeImageError,
)
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


async def _ask_gpt_to_rewrite(
    gpt: ChatGPTBot,
    original_prompt: str,
    *,
    project_id: int | None = None,
) -> str | None:
    """Запрашивает у ChatGPT переписанный промт без триггеров модерации.
    Возвращает stripped-текст, либо None если rewrite не получился."""
    full_request = f"{_GPT_REWRITE_META}\n\n{original_prompt}"
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
                return await outsee.generate_image(
                    current_prompt, out_path, **attempt_kwargs
                )
            except StepCancelledError:
                raise
            except OutseeImageError as e:
                last_err = e
                logger.warning(
                    "outsee.generate_image [{}] попытка {}/{} (id={}) "
                    "провалена: {}",
                    round_label, attempt, max_attempts_per_prompt,
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
            break
        rewritten = await _ask_gpt_to_rewrite(
            gpt,
            current_prompt,
            project_id=pid if isinstance(pid, int) else None,
        )
        if not rewritten:
            break  # rewrite не получился — выходим с последней ошибкой
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
    **kwargs: Any,
) -> GenerationResult:
    """Аналог `generate_image_with_retries` для видео-генерации.
    Логика идентична: 3 попытки → GPT-rewrite → ещё 3 попытки.

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

    for round_idx, round_label in enumerate(rounds):
        for attempt in range(1, max_attempts_per_prompt + 1):
            abort_if_cancelled(project_id)
            attempt_kwargs = dict(kwargs)
            attempt_kwargs["prompt_id_prefix"] = _uniquify_prompt_id(
                base_prompt_id, round_idx, attempt
            )
            try:
                return await outsee.generate_video(
                    current_prompt, out_path, project_id=project_id,
                    **attempt_kwargs,
                )
            except StepCancelledError:
                raise
            except OutseeImageError as e:
                last_err = e
                logger.warning(
                    "outsee.generate_video [{}] попытка {}/{} (id={}) "
                    "провалена: {}",
                    round_label, attempt, max_attempts_per_prompt,
                    attempt_kwargs.get("prompt_id_prefix") or "—",
                    e.reason,
                )
                if attempt < max_attempts_per_prompt:
                    await sleep_cancellable(2.0, project_id)

        is_last_round = round_idx == len(rounds) - 1
        if is_last_round:
            break
        if gpt is None:
            break
        rewritten = await _ask_gpt_to_rewrite(
            gpt, current_prompt, project_id=project_id
        )
        if not rewritten:
            break
        current_prompt = rewritten

    if last_err is None:
        raise RuntimeError("generate_video_with_retries: unreachable")
    raise last_err
