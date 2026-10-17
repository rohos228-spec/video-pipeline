"""Ключ и replay LLM-фикстур.

Формула ключа и тело файла — ``app/orchestrator/routing/CONTRACTS.md`` и
``hashing.py`` из PR #345 (ветка ``cursor/ws5-model-router-9a0d``). Код WS5
не импортируется.

``key = sha256(canonical_json{tier, messages, temperature, max_tokens, reasoning_effort})``.
``canonical_json``: UTF-8, ``sort_keys``, разделители ``(',', ':')``, ``ensure_ascii=False``.
Целые ``temperature`` / ``max_tokens`` кодируются как int (``1`` и ``1.0`` — один ключ).
Сообщения схлопываются до ``{role, content}``.

Тело ``request_meta`` не содержит текст сообщений: только tier, temperature,
max_tokens, reasoning_effort и ``messages_sha256``. Нет файла или ``key`` внутри
не совпал → ``FixtureMiss``. Живой вызов запрещён.

D1/D6 этот адаптер не применяет: в ключе ровно аргументы ``call``. Роутер WS5
подставляет tier и reasoning_effort primary-хопа до хеша. Чтобы файл читался
обоими replay, вызывающий передаёт уже разрешённые значения.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from app.orchestrator.rag.canon import canonical_json, sha256_payload, sha256_text
from app.orchestrator.rag.types import FixtureMiss, LLMResponse

Clock = Callable[[], str]


def _content(value: Any) -> str:
    if isinstance(value, str):
        return value
    return canonical_json(value)


def normalize_messages(messages: list[Any] | None) -> list[dict[str, str]]:
    """Как ``routing.hashing.normalize_messages``: только role и content."""
    out: list[dict[str, str]] = []
    for raw in messages or []:
        if isinstance(raw, str):
            out.append({"role": "user", "content": raw})
            continue
        if not isinstance(raw, dict):
            raise TypeError("элемент messages должен быть объектом или строкой")
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
    tier: str,
    messages: list[Any] | None,
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


def messages_sha256(messages: list[Any] | None) -> str:
    return sha256_text(canonical_json(normalize_messages(messages)))


def request_meta(
    tier: str,
    messages: list[Any] | None,
    temperature: float | None,
    max_tokens: int | None,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    """Тело ``request_meta`` по CONTRACTS.md. Текст сообщений в файл не пишется."""
    payload = fixture_payload(tier, messages, temperature, max_tokens, reasoning_effort)
    return {
        "tier": payload["tier"],
        "temperature": payload["temperature"],
        "max_tokens": payload["max_tokens"],
        "reasoning_effort": payload["reasoning_effort"],
        "messages_sha256": messages_sha256(messages),
    }


def fixture_key(
    tier: str,
    messages: list[Any] | None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> str:
    return sha256_payload(fixture_payload(tier, messages, temperature, max_tokens, reasoning_effort))


class Router(Protocol):
    """Порт ``router.call(tier|auto, messages, stream) -> LLMResponse`` (WS5)."""

    def call(
        self,
        tier: str,
        messages: list[dict[str, Any]],
        stream: bool = False,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMResponse: ...


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class ReplayRouter:
    """Воспроизведение фикстур. Промах — ``FixtureMiss``, без фолбэка в сеть.

    ``call_id`` ответа — новый uuid на прогон. Записанный ``call_id`` остаётся
    только в файле (UNIQUE в ``orch_llm_calls`` у WS5).
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def call(
        self,
        tier: str,
        messages: list[dict[str, Any]],
        stream: bool = False,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        del stream  # ключ фикстуры stream не включает; ответ всё равно целый текст
        key = fixture_key(tier, messages, temperature, max_tokens, reasoning_effort)
        path = self.directory / f"{key}.json"
        if not path.is_file():
            raise FixtureMiss(key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise FixtureMiss(key) from exc
        if payload.get("key") != key:
            raise FixtureMiss(key)
        response = LLMResponse.from_dict(payload.get("response") or {})
        response.fixture_key = key
        response.call_id = str(uuid.uuid4())
        return response


class RecordingRouter:
    """Пишет один JSON ``{key}.json``. Внутренний роутер не обязан быть сетевым."""

    def __init__(self, inner: Router, directory: Path, *, clock: Clock | None = None) -> None:
        self.inner = inner
        self.directory = Path(directory)
        self.clock = clock or _utc_now

    def call(
        self,
        tier: str,
        messages: list[dict[str, Any]],
        stream: bool = False,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        meta = request_meta(tier, messages, temperature, max_tokens, reasoning_effort)
        key = fixture_key(tier, messages, temperature, max_tokens, reasoning_effort)
        response = self.inner.call(
            tier,
            messages,
            stream,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
        response.fixture_key = key
        body = {
            "key": key,
            "request_meta": meta,
            "response": response.to_dict(),
            "recorded_at": self.clock(),
            "recorded_model": response.model,
        }
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / f"{key}.json"
        path.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return response


class ScriptedRouter:
    """Детерминированные ответы для механики (FSM, interrupt). Не ходит в сеть."""

    def __init__(self, handler: Callable[..., LLMResponse]) -> None:
        self._handler = handler

    def call(
        self,
        tier: str,
        messages: list[dict[str, Any]],
        stream: bool = False,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        return self._handler(
            tier,
            messages,
            stream,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
