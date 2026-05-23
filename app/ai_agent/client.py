"""OpenAI-совместимый клиент через aitunnel.ru (или прямой OpenAI).

Использует `aiohttp` напрямую (без openai SDK), чтобы:
1) не плодить зависимости — у нас уже стоит aiohttp.
2) переиспользовать ту же логику в orchestrator_api._ai_plan_command.

Поддерживает:
- chat.completions с tools (function-calling).
- Multi-turn (история сообщений).
- Custom base_url (aitunnel, OpenAI, OpenRouter).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import aiohttp
from loguru import logger

from app.ai_agent.config import AIAgentConfig


class AIClientError(RuntimeError):
    """Ошибка при работе с LLM API (network/auth/limit/etc)."""


@dataclass
class AIChatResponse:
    """Распарсенный ответ /chat/completions."""

    content: str | None
    tool_calls: list[dict[str, Any]]  # OpenAI format
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_rub: float | None  # aitunnel возвращает это в usage
    raw: dict[str, Any]


class AIClient:
    """Тонкий клиент над OpenAI-совместимым endpoint'ом."""

    def __init__(self, config: AIAgentConfig) -> None:
        if not config.is_configured:
            raise AIClientError(
                "AI-агент не сконфигурирован. Добавь ORCHESTRATOR_AI_API_KEY "
                "в .env (см. .env.example)."
            )
        self.cfg = config

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = "auto",
        temperature: float = 0.2,
        max_tokens: int = 4096,
        timeout_sec: float = 60.0,
    ) -> AIChatResponse:
        """Сделать одиночный вызов /chat/completions.

        messages — OpenAI формат: [{role, content}, {role, tool_calls}, ...].
        tools — JSON-schema'ы инструментов (см. app.ai_agent.tools).
        """
        url = f"{self.cfg.base_url}/chat/completions"
        body: dict[str, Any] = {
            "model": model or self.cfg.default_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            if tool_choice is not None:
                body["tool_choice"] = tool_choice

        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type": "application/json",
        }

        logger.debug(
            "ai_agent.client.chat: model={} msgs={} tools={} max_tokens={}",
            body["model"],
            len(messages),
            len(tools) if tools else 0,
            max_tokens,
        )

        timeout = aiohttp.ClientTimeout(total=timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout) as sess:
            try:
                async with sess.post(url, json=body, headers=headers) as resp:
                    raw_text = await resp.text()
                    if resp.status >= 400:
                        raise AIClientError(
                            f"AI API {resp.status} from {self.cfg.base_url}: "
                            f"{raw_text[:500]}"
                        )
                    payload = json.loads(raw_text)
            except asyncio.TimeoutError as e:  # noqa: UP041
                raise AIClientError(
                    f"AI API timeout ({timeout_sec}s) at {self.cfg.base_url}"
                ) from e
            except aiohttp.ClientError as e:
                raise AIClientError(f"AI API network error: {e}") from e

        try:
            choice = payload["choices"][0]
            msg = choice["message"]
            content = msg.get("content") or None
            tool_calls = msg.get("tool_calls") or []
            finish_reason = choice.get("finish_reason", "stop")
            usage = payload.get("usage", {})
        except (KeyError, IndexError, TypeError) as e:
            raise AIClientError(
                f"AI API malformed response: {payload}"
            ) from e

        return AIChatResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            total_tokens=int(usage.get("total_tokens", 0)),
            cost_rub=(
                float(usage.get("cost_rub"))
                if usage.get("cost_rub") is not None
                else None
            ),
            raw=payload,
        )

    async def check_balance(self) -> float | None:
        """AITunnel-специфичный endpoint: GET /aitunnel/balance.

        Возвращает баланс в рублях, либо None если эндпоинт не отвечает
        (например, мы за прямым OpenAI).
        """
        url = f"{self.cfg.base_url}/aitunnel/balance"
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"}
        timeout = aiohttp.ClientTimeout(total=15.0)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as sess:  # noqa: SIM117
                async with sess.get(url, headers=headers) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.json()
                    bal = data.get("balance")
                    return float(bal) if bal is not None else None
        except (TimeoutError, aiohttp.ClientError, ValueError):
            return None
