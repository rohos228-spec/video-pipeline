"""HTTP-транспорт 52pay (OpenAI chat) и Kie (Responses). Тесты подменяют ``complete``."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from app.orchestrator.routing.config import ProviderConfig
from app.orchestrator.routing.wire import (
    PAY52_MODULE_MISSING,
    kie_model_slug,
    pay52_base_url,
    resolve_pay52,
)


class ProviderTimeout(Exception):
    pass


class ProviderError(Exception):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


@dataclass
class ProviderRequest:
    model: str
    provider: str
    messages: list[dict[str, str]]
    temperature: float | None
    max_tokens: int | None
    reasoning_effort: str | None
    timeout_s: float
    stream: bool = False
    on_delta: Any = None
    url: str | None = None
    api_key: str | None = None


@dataclass
class CompletionResult:
    text: str
    finish_reason: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0


def join_url(base: str, path: str) -> str:
    root = base.rstrip("/")
    suffix = "/" + path.lstrip("/")
    if root.endswith(suffix.rstrip("/")):
        return root
    if root.endswith("/v1") and suffix.startswith("/v1/"):
        return root + suffix[len("/v1") :]
    return root + suffix


def build_body(request: ProviderRequest, api: str) -> dict[str, Any]:
    if api == "openai-responses":
        body: dict[str, Any] = {
            "model": request.model,
            "input": [
                {"role": item["role"], "content": item["content"]}
                for item in request.messages
            ],
            "stream": bool(request.stream),
            "store": False,
        }
        if request.reasoning_effort:
            body["reasoning"] = {"effort": request.reasoning_effort}
        return body
    body = {"model": request.model, "messages": list(request.messages)}
    if request.temperature is not None:
        body["temperature"] = request.temperature
    if request.max_tokens is not None:
        body["max_tokens"] = request.max_tokens
    if request.reasoning_effort:
        body["reasoning_effort"] = request.reasoning_effort
    if request.stream:
        body["stream"] = True
    return body


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_chat_payload(payload: dict[str, Any]) -> CompletionResult:
    choices = payload.get("choices") or []
    if not choices:
        raise ProviderError("empty chat completion", retryable=True)
    choice = choices[0] or {}
    message = choice.get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        text = "".join(
            part.get("text", "") for part in content if isinstance(part, dict)
        )
    else:
        text = str(content or "")
    usage = payload.get("usage") or {}
    details = usage.get("completion_tokens_details") or {}
    reasoning = usage.get("reasoning_tokens", details.get("reasoning_tokens"))
    return CompletionResult(
        text=text,
        finish_reason=str(choice.get("finish_reason") or ""),
        model=str(payload.get("model") or ""),
        prompt_tokens=_int(usage.get("prompt_tokens")),
        completion_tokens=_int(usage.get("completion_tokens")),
        reasoning_tokens=_int(reasoning),
    )


def parse_chat_sse(raw: str) -> CompletionResult:
    parts: list[str] = []
    finish = ""
    model = ""
    usage: dict[str, Any] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        data = stripped[len("data:") :].strip()
        if not data or data == "[DONE]":
            continue
        payload = json.loads(data)
        model = str(payload.get("model") or model)
        if payload.get("usage"):
            usage = payload["usage"]
        for choice in payload.get("choices") or []:
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece is None and choice.get("message"):
                piece = (choice.get("message") or {}).get("content")
            if isinstance(piece, str):
                parts.append(piece)
            if choice.get("finish_reason"):
                finish = str(choice["finish_reason"])
    details = usage.get("completion_tokens_details") or {}
    return CompletionResult(
        text="".join(parts),
        finish_reason=finish,
        model=model,
        prompt_tokens=_int(usage.get("prompt_tokens")),
        completion_tokens=_int(usage.get("completion_tokens")),
        reasoning_tokens=_int(
            usage.get("reasoning_tokens", details.get("reasoning_tokens"))
        ),
    )


def parse_responses_sse(raw: str) -> CompletionResult:
    """Собрать текст из SSE Responses API (``response.output_text.delta``)."""
    parts: list[str] = []
    done_text = ""
    model = ""
    finish = ""
    usage: dict[str, Any] = {}
    completed: dict[str, Any] | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped.startswith("data:"):
            continue
        data = stripped[len("data:") :].strip()
        if not data or data == "[DONE]":
            continue
        payload = json.loads(data)
        if not isinstance(payload, dict):
            continue
        etype = str(payload.get("type") or "")
        if etype == "response.output_text.delta" and payload.get("delta"):
            parts.append(str(payload["delta"]))
        elif etype == "response.output_text.done":
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                done_text = text
        elif etype == "response.completed" and isinstance(payload.get("response"), dict):
            completed = payload["response"]
            model = str(completed.get("model") or model)
            finish = str(completed.get("status") or "completed")
            if isinstance(completed.get("usage"), dict):
                usage = completed["usage"]
    text = "".join(parts)
    if len(done_text) > len(text):
        text = done_text
    parsed: CompletionResult | None = None
    if completed is not None:
        try:
            parsed = parse_responses_payload(completed)
        except ProviderError:
            parsed = None
    if parsed is not None and len(parsed.text) > len(text):
        text = parsed.text
    if parsed is not None:
        model = parsed.model or model
        finish = parsed.finish_reason or finish
        if parsed.prompt_tokens or parsed.completion_tokens or parsed.reasoning_tokens:
            usage = usage or {}
    if not text:
        raise ProviderError("empty responses stream", retryable=True)
    details = usage.get("output_tokens_details") or {}
    reasoning = parsed.reasoning_tokens if parsed is not None else _int(details.get("reasoning_tokens"))
    prompt = parsed.prompt_tokens if parsed is not None else _int(usage.get("input_tokens"))
    completion = (
        parsed.completion_tokens if parsed is not None else _int(usage.get("output_tokens"))
    )
    return CompletionResult(
        text=text,
        finish_reason=finish or "completed",
        model=model,
        prompt_tokens=prompt,
        completion_tokens=completion,
        reasoning_tokens=reasoning,
    )


def parse_responses_payload(payload: dict[str, Any]) -> CompletionResult:
    parts: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        for block in content or []:
            if isinstance(block, dict) and block.get("text"):
                parts.append(str(block["text"]))
    if not parts and payload.get("output_text"):
        parts.append(str(payload["output_text"]))
    usage = payload.get("usage") or {}
    details = usage.get("output_tokens_details") or {}
    text = "".join(parts)
    if not text:
        raise ProviderError("empty responses output", retryable=True)
    return CompletionResult(
        text=text,
        finish_reason=str(payload.get("status") or "completed"),
        model=str(payload.get("model") or ""),
        prompt_tokens=_int(usage.get("input_tokens")),
        completion_tokens=_int(usage.get("output_tokens")),
        reasoning_tokens=_int(details.get("reasoning_tokens")),
    )


_RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 524}


class HttpxTransport:
    """Один POST. Ретраи и фолбэк делает роутер, не транспорт."""

    def __init__(
        self,
        providers: Mapping[str, ProviderConfig],
        *,
        env: Mapping[str, str] | None = None,
        client_factory: Any = None,
        pay52_resolver: Any = None,
    ) -> None:
        self.providers = providers
        self.env = env if env is not None else __import__("os").environ
        self._client_factory = client_factory
        self._pay52_resolver = pay52_resolver or resolve_pay52

    def _client(self, timeout_s: float) -> httpx.AsyncClient:
        timeout = httpx.Timeout(timeout_s, connect=min(10.0, timeout_s))
        if self._client_factory is not None:
            return self._client_factory(timeout)
        return httpx.AsyncClient(timeout=timeout)

    def _endpoint(self, request: ProviderRequest, cfg: ProviderConfig) -> tuple[str, str]:
        """(url, api_key). 52pay — из ``resolve_text``, иначе settings/env."""
        if request.provider == "kie":
            request.stream = True
            request.model = kie_model_slug()
        if request.provider == "52pay" and not request.url:
            route = self._pay52_resolver(request.model)
            if route is PAY52_MODULE_MISSING:
                pass
            elif route is None:
                raise ProviderError("pay52 route unavailable", retryable=False)
            else:
                request.model = str(route.api_model)
                request.url = str(route.url)
                request.api_key = str(route.api_key)
        if request.api_key:
            key = request.api_key.strip()
        else:
            key = (self.env.get(cfg.api_key_env) or "").strip()
            if not key and cfg.alt_api_key_env:
                key = (self.env.get(cfg.alt_api_key_env) or "").strip()
        if not key:
            raise ProviderError(
                f"missing api key ({cfg.api_key_env})", retryable=False
            )
        if request.url:
            return request.url, key
        base = (self.env.get(cfg.base_url_env) or "").strip()
        if not base and request.provider == "52pay":
            base = pay52_base_url()
        if not base:
            base = (cfg.default_base_url or "").strip()
        if not base:
            raise ProviderError(
                f"missing base url ({cfg.base_url_env})", retryable=False
            )
        return join_url(base, cfg.chat_path), key

    async def complete(self, request: ProviderRequest) -> CompletionResult:
        cfg = self.providers.get(request.provider)
        if cfg is None:
            raise ProviderError(
                f"unknown provider {request.provider}", retryable=False
            )
        url, key = self._endpoint(request, cfg)
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        if cfg.relay_token_env:
            relay = (self.env.get(cfg.relay_token_env) or "").strip()
            if relay:
                headers["X-VP-Relay-Token"] = relay
        body = build_body(request, cfg.api)
        try:
            async with self._client(request.timeout_s) as client:
                if request.stream and cfg.api == "openai-responses":
                    return await self._stream_responses(client, url, headers, body, request)
                if request.stream:
                    return await self._stream_chat(client, url, headers, body, request)
                response = await client.post(url, headers=headers, json=body)
        except httpx.TimeoutException as exc:
            raise ProviderTimeout(str(exc) or "timeout") from exc
        except httpx.TransportError as exc:
            raise ProviderError(str(exc) or "transport", retryable=True) from exc
        if response.status_code >= 400:
            raise ProviderError(
                f"http {response.status_code}",
                retryable=response.status_code in _RETRY_STATUS,
                status_code=response.status_code,
            )
        payload = response.json()
        if cfg.api == "openai-responses":
            result = parse_responses_payload(payload)
        else:
            result = parse_chat_payload(payload)
        if not result.model:
            result.model = request.model
        return result

    async def _stream_chat(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        request: ProviderRequest,
    ) -> CompletionResult:
        parts: list[str] = []
        finish = ""
        model = request.model
        usage: dict[str, Any] = {}
        async with client.stream("POST", url, headers=headers, json=body) as response:
            if response.status_code >= 400:
                raise ProviderError(
                    f"http {response.status_code}",
                    retryable=response.status_code in _RETRY_STATUS,
                    status_code=response.status_code,
                )
            async for line in response.aiter_lines():
                stripped = line.strip()
                if not stripped.startswith("data:"):
                    continue
                data = stripped[len("data:") :].strip()
                if not data or data == "[DONE]":
                    continue
                payload = json.loads(data)
                model = str(payload.get("model") or model)
                if payload.get("usage"):
                    usage = payload["usage"]
                for choice in payload.get("choices") or []:
                    delta = (choice.get("delta") or {}).get("content")
                    if isinstance(delta, str) and delta:
                        parts.append(delta)
                        if request.on_delta is not None:
                            request.on_delta(delta)
                    if choice.get("finish_reason"):
                        finish = str(choice["finish_reason"])
        details = usage.get("completion_tokens_details") or {}
        return CompletionResult(
            text="".join(parts),
            finish_reason=finish,
            model=model,
            prompt_tokens=_int(usage.get("prompt_tokens")),
            completion_tokens=_int(usage.get("completion_tokens")),
            reasoning_tokens=_int(
                usage.get("reasoning_tokens", details.get("reasoning_tokens"))
            ),
        )

    async def _stream_responses(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        request: ProviderRequest,
    ) -> CompletionResult:
        """Kie: только stream. Нестриминговый reasoning часто отвечает 524."""
        lines: list[str] = []
        async with client.stream("POST", url, headers=headers, json=body) as response:
            if response.status_code >= 400:
                raise ProviderError(
                    f"http {response.status_code}",
                    retryable=response.status_code in _RETRY_STATUS,
                    status_code=response.status_code,
                )
            async for line in response.aiter_lines():
                lines.append(line)
                stripped = line.strip()
                if not stripped.startswith("data:"):
                    continue
                data = stripped[len("data:") :].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    payload = json.loads(data)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(payload, dict)
                    and payload.get("type") == "response.output_text.delta"
                    and payload.get("delta")
                    and request.on_delta is not None
                ):
                    request.on_delta(str(payload["delta"]))
        result = parse_responses_sse("\n".join(lines))
        if not result.model:
            result.model = request.model
        return result
