"""Канонический ключ LLM-фикстуры.

key = sha256(canonical_json{tier, messages, temperature, max_tokens, reasoning_effort})

canonical_json: UTF-8, ``sort_keys``, разделители ``(',', ':')``, ``ensure_ascii=False``.
Целые temperature/max_tokens кодируются как int (``1`` и ``1.0`` — один ключ).
Сообщения схлопываются до ``{role, content}``; лишние поля в хеш не входят.
Регистр и пробелы не нормализуются.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content(value: Any) -> str:
    if isinstance(value, str):
        return value
    return canonical_json(value)


def normalize_messages(messages: list[Any] | None) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for raw in messages or []:
        if isinstance(raw, str):
            out.append({"role": "user", "content": raw})
            continue
        out.append(
            {
                "role": str(raw.get("role") or "user"),
                "content": _content(raw.get("content", "")),
            }
        )
    return out


def _whole_number(value: Any) -> Any:
    if value is None or value == "":
        return None
    number = float(value)
    if number.is_integer():
        return int(number)
    return round(number, 6)


def fixture_payload(
    *,
    tier: str,
    messages: list[Any],
    temperature: float | None,
    max_tokens: int | None,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    effort = (reasoning_effort or None) if reasoning_effort != "" else None
    return {
        "max_tokens": None if max_tokens is None else int(max_tokens),
        "messages": normalize_messages(messages),
        "reasoning_effort": effort,
        "temperature": _whole_number(temperature),
        "tier": tier,
    }


def fixture_key(
    *,
    tier: str,
    messages: list[Any],
    temperature: float | None,
    max_tokens: int | None,
    reasoning_effort: str | None,
) -> str:
    raw = canonical_json(
        fixture_payload(
            tier=tier,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def messages_sha256(messages: list[Any]) -> str:
    raw = canonical_json(normalize_messages(messages))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
