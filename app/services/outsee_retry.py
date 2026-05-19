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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from loguru import logger

from app.bots.chatgpt import ChatGPTBot
from app.bots.outsee import (
    GenerationResult,
    OutseeBot,
    OutseeImageError,
)

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
    gpt: ChatGPTBot, original_prompt: str
) -> str | None:
    """Запрашивает у ChatGPT переписанный промт без триггеров модерации.
    Возвращает stripped-текст, либо None если rewrite не получился."""
    full_request = f"{_GPT_REWRITE_META}\n\n{original_prompt}"
    try:
        reply = await gpt.ask_fresh(full_request, timeout=600)
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
    """
    last_err: OutseeImageError | None = None
    current_prompt = prompt
    rounds: list[tuple[str, str]] = [("original", current_prompt)]
    if gpt_rewrite and gpt is not None:
        rounds.append(("rewritten", ""))  # placeholder, заполним если дойдём

    for round_idx, (round_label, _) in enumerate(rounds):
        for attempt in range(1, max_attempts_per_prompt + 1):
            try:
                return await outsee.generate_image(
                    current_prompt, out_path, **kwargs
                )
            except OutseeImageError as e:
                last_err = e
                logger.warning(
                    "outsee.generate_image [{}] попытка {}/{} "
                    "провалена: {}",
                    round_label, attempt, max_attempts_per_prompt,
                    e.reason,
                )
                if attempt < max_attempts_per_prompt:
                    await asyncio.sleep(2.0)

        # Все попытки в этом раунде провалились. Если ещё есть раунд
        # «rewritten» — попробуем переписать промт через GPT.
        is_last_round = round_idx == len(rounds) - 1
        if is_last_round:
            break
        if gpt is None:
            break
        rewritten = await _ask_gpt_to_rewrite(gpt, current_prompt)
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
    out_path: Path | None = None,
    out_path_factory: Callable[[int], Path] | None = None,
    prompt_id_prefix_factory: Callable[[int], str] | None = None,
    max_attempts_per_prompt: int = 3,
    gpt_rewrite: bool = True,
    **kwargs: Any,
) -> GenerationResult:
    """Аналог `generate_image_with_retries` для видео-генерации.
    Логика идентична: 3 попытки → GPT-rewrite → ещё 3 попытки.

    `outsee.generate_video` бросает тот же базовый класс `OutseeImageError`
    при ошибках UI-уровня (не нашлась кнопка / таймаут), поэтому мы
    переиспользуем тот же except-handler.

    КЛЮЧЕВОЕ ОТЛИЧИЕ от image-retries: для видео НА КАЖДОЙ попытке
    желательно генерировать НОВЫЙ `prompt_id_prefix` (и соответствующий
    `out_path`). Иначе после первой провальной попытки в outsee
    остаётся карточка-«Ошибка» с тем же `[ID:]`, и `_wait_video_url`
    немедленно ловит ЕЁ вместо новой генерации — все ретраи становятся
    no-op'ом.

    Каллер передаёт `prompt_id_prefix_factory(attempt_no)` и
    `out_path_factory(attempt_no)`. `attempt_no` сквозной (1..N),
    не сбрасывается между round'ами (original / rewritten).

    Для обратной совместимости можно по-прежнему передавать `out_path`
    и `prompt_id_prefix` через kwargs (legacy) — тогда все попытки
    используют один и тот же id. Это путь для не-видео вызовов.
    """
    last_err: OutseeImageError | None = None
    current_prompt = prompt
    rounds: list[str] = ["original"]
    if gpt_rewrite and gpt is not None:
        rounds.append("rewritten")

    overall_attempt = 0
    for round_idx, round_label in enumerate(rounds):
        for attempt in range(1, max_attempts_per_prompt + 1):
            overall_attempt += 1
            this_out_path: Path | None
            if out_path_factory is not None:
                this_out_path = out_path_factory(overall_attempt)
            else:
                this_out_path = out_path
            if this_out_path is None:
                raise ValueError(
                    "generate_video_with_retries: нужен out_path или "
                    "out_path_factory"
                )
            this_kwargs: dict[str, Any] = dict(kwargs)
            if prompt_id_prefix_factory is not None:
                this_kwargs["prompt_id_prefix"] = prompt_id_prefix_factory(
                    overall_attempt
                )
            try:
                return await outsee.generate_video(
                    current_prompt, this_out_path, **this_kwargs
                )
            except OutseeImageError as e:
                last_err = e
                # Юзер нажал ⏹ Стоп шаг внутри _wait_video_url —
                # ретраить нет смысла, шаг должен немедленно завершиться.
                # generate_videos.py обернёт OutseeImageError в
                # StepCancelledError. Здесь — просто пробрасываем.
                if "cancelled by user" in (e.reason or "").lower():
                    raise
                logger.warning(
                    "outsee.generate_video [{}] попытка {}/{} "
                    "(overall {}) провалена: {}",
                    round_label, attempt, max_attempts_per_prompt,
                    overall_attempt, e.reason,
                )
                if attempt < max_attempts_per_prompt:
                    await asyncio.sleep(2.0)

        is_last_round = round_idx == len(rounds) - 1
        if is_last_round:
            break
        if gpt is None:
            break
        rewritten = await _ask_gpt_to_rewrite(gpt, current_prompt)
        if not rewritten:
            break
        current_prompt = rewritten

    if last_err is None:
        raise RuntimeError("generate_video_with_retries: unreachable")
    raise last_err
