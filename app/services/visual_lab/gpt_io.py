"""JSON-extraction helpers for ChatGPT replies.

ChatGPT (via the browser bot) returns free-form text. We need a strict
JSON object back from analyze/think/build phases, so:

1. ``extract_json_object`` finds the largest valid JSON object in a reply
   (handles fenced code blocks, leading commentary, trailing prose).
2. ``ask_gpt_json`` wraps a one-shot exchange with up to
   ``MAX_PHASE_RETRIES`` retries — on each retry it nudges GPT to return
   pure JSON ("your last response was not valid JSON, try again, …").

All retry budgets come from :mod:`app.services.visual_lab.limits` so
they're consistent across phases.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, ValidationError

from app.services.visual_lab.limits import MAX_PHASE_RETRIES

# Patterns for unwrapping ```json fences.
_FENCED_JSON = re.compile(r"```(?:json|JSON)?\s*(\{.*?\})\s*```", re.DOTALL)


class GPTJSONError(RuntimeError):
    """Raised when GPT fails to return valid JSON after all retries."""


def extract_json_object(text: str) -> dict[str, Any]:
    """Extract the largest valid JSON object from a free-form reply.

    Strategy:
      1. Try fenced ```json blocks first (most reliable).
      2. Fall back to scanning for balanced ``{...}`` and json.loads each.
      3. Return the largest one (by character count) that parses.

    Raises ``ValueError`` if nothing parses.
    """
    text = (text or "").strip()
    if not text:
        raise ValueError("empty response")

    candidates: list[str] = []
    candidates.extend(m.group(1) for m in _FENCED_JSON.finditer(text))
    candidates.extend(_find_balanced_objects(text))

    parsed: list[tuple[int, dict[str, Any]]] = []
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            parsed.append((len(cand), obj))

    if not parsed:
        raise ValueError(
            f"no valid JSON object in {len(text)}-char response "
            f"(first 200: {text[:200]!r})"
        )

    parsed.sort(key=lambda x: x[0], reverse=True)
    return parsed[0][1]


def _find_balanced_objects(text: str) -> list[str]:
    """Yield substrings of ``text`` that look like balanced { ... } objects.

    A simple stack scanner — not a full JSON parser, but enough for our
    "GPT wrote some prose, then a JSON object, then a sign-off" case.
    Skips braces inside string literals (with backslash escapes).
    """
    results: list[str] = []
    stack: list[int] = []
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            start = stack.pop()
            if not stack:
                results.append(text[start : i + 1])
    return results


_RETRY_NUDGE = (
    "Your previous response could not be parsed as a JSON object. "
    "Respond with ONLY one valid JSON object (no prose, no markdown, no "
    "code fences), matching exactly the schema I described above. "
    "Do not include explanations outside the JSON."
)


async def ask_gpt_json(
    *,
    ask_fn: Callable[..., Awaitable[str]],
    base_prompt: str,
    attachments: list[Path] | None = None,
    schema_hint: str = "",
    retries: int = MAX_PHASE_RETRIES,
    chat_log_dir: Path | None = None,
    label: str = "gpt_json",
) -> dict[str, Any]:
    """Ask GPT for a JSON object, retrying on parse failures.

    ``ask_fn`` is a coroutine that takes (prompt, attachments=...) and
    returns the raw reply text. Typically a closure around
    ``ChatGPTBot.ask`` or ``ChatGPTBot.ask_with_files``.

    On each failure we re-send the same base prompt plus
    ``_RETRY_NUDGE``. Raises ``GPTJSONError`` after ``retries`` attempts.
    """
    last_error: Exception | None = None
    last_reply: str = ""

    for attempt in range(1, retries + 1):
        suffix = "" if attempt == 1 else f"\n\n{_RETRY_NUDGE}"
        if schema_hint and attempt > 1:
            suffix += f"\n\nSchema reminder:\n{schema_hint}"
        prompt = base_prompt + suffix

        try:
            if attachments:
                reply = await ask_fn(prompt, attachments=attachments)
            else:
                reply = await ask_fn(prompt)
        except Exception as e:  # noqa: BLE001
            last_error = e
            logger.warning(
                "visual_lab.gpt_io[{}]: ask_fn raised on attempt {}/{}: {}",
                label, attempt, retries, e,
            )
            continue

        last_reply = reply or ""
        if chat_log_dir is not None:
            try:
                chat_log_dir.mkdir(parents=True, exist_ok=True)
                (chat_log_dir / f"{label}_attempt_{attempt}.txt").write_text(
                    last_reply, encoding="utf-8"
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("visual_lab.gpt_io: cannot dump chat log: {}", e)

        try:
            return extract_json_object(last_reply)
        except ValueError as e:
            last_error = e
            logger.warning(
                "visual_lab.gpt_io[{}]: JSON parse failed attempt {}/{}: {}",
                label, attempt, retries, e,
            )

    raise GPTJSONError(
        f"GPT did not return valid JSON for '{label}' after {retries} "
        f"attempts: {last_error}"
    )


async def ask_gpt_validated(
    *,
    ask_fn: Callable[..., Awaitable[str]],
    base_prompt: str,
    model: type[BaseModel],
    attachments: list[Path] | None = None,
    schema_hint: str = "",
    retries: int = MAX_PHASE_RETRIES,
    chat_log_dir: Path | None = None,
    label: str = "gpt_validated",
) -> BaseModel:
    """``ask_gpt_json`` + Pydantic validation against ``model``.

    Validation errors trigger an extra retry round with the validation
    error appended as a nudge, so GPT can self-correct.
    """
    last_validation_error: Exception | None = None
    for round_ in range(1, retries + 1):
        nudge = ""
        if last_validation_error is not None:
            nudge = (
                f"\n\nYour previous JSON did not match the schema. "
                f"Validation error: {last_validation_error}. Fix it and "
                f"return JUST the corrected JSON object."
            )

        obj = await ask_gpt_json(
            ask_fn=ask_fn,
            base_prompt=base_prompt + nudge,
            attachments=attachments,
            schema_hint=schema_hint,
            retries=retries,
            chat_log_dir=chat_log_dir,
            label=f"{label}_round{round_}",
        )
        try:
            return model.model_validate(obj)
        except ValidationError as e:
            last_validation_error = e
            logger.warning(
                "visual_lab.gpt_io[{}]: pydantic validation failed "
                "round {}/{}: {}",
                label, round_, retries, e,
            )

    raise GPTJSONError(
        f"GPT JSON did not validate against {model.__name__} after "
        f"{retries} rounds: {last_validation_error}"
    )
