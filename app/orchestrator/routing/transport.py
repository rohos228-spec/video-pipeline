"""HTTP-транспорт 52pay (OpenAI chat) и Kie (Responses). Тесты подменяют ``complete``."""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
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
        # Без этого флага OpenAI-совместимый SSE не присылает usage,
        # и ход выглядит бесплатным для лимита.
        body["stream_options"] = {"include_usage": True}
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


def _json_data(line: str) -> dict[str, Any] | None:
    """Один SSE-event. Битый JSON и keep-alive (``:`` / пустая строка) — None."""
    stripped = line.strip()
    if not stripped or stripped.startswith(":"):
        return None
    if not stripped.startswith("data:"):
        return None
    data = stripped[len("data:") :].strip()
    if not data or data == "[DONE]":
        return None
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _chat_deltas(payload: dict[str, Any]) -> list[str]:
    pieces: list[str] = []
    for choice in payload.get("choices") or []:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta") or {}
        piece = delta.get("content") if isinstance(delta, dict) else None
        if piece is None and isinstance(choice.get("message"), dict):
            piece = choice["message"].get("content")
        if isinstance(piece, str) and piece:
            pieces.append(piece)
    return pieces


def _chat_finish(payload: dict[str, Any]) -> str:
    finish = ""
    for choice in payload.get("choices") or []:
        if isinstance(choice, dict) and choice.get("finish_reason"):
            finish = str(choice["finish_reason"])
    return finish


def _chat_reasoning(usage: dict[str, Any]) -> int:
    details = usage.get("completion_tokens_details") or {}
    return _int(usage.get("reasoning_tokens", details.get("reasoning_tokens")))


@dataclass
class _ChatStream:
    """Общий разбор chat SSE для ``parse_chat_sse`` и ``_stream_chat``."""

    parts: list[str] = field(default_factory=list)
    finish: str = ""
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)

    def feed(self, payload: dict[str, Any]) -> list[str]:
        self.model = str(payload.get("model") or self.model)
        if isinstance(payload.get("usage"), dict):
            self.usage = payload["usage"]
        deltas = _chat_deltas(payload)
        self.parts.extend(deltas)
        finish = _chat_finish(payload)
        if finish:
            self.finish = finish
        return deltas

    def result(self) -> CompletionResult:
        return CompletionResult(
            text="".join(self.parts),
            finish_reason=self.finish,
            model=self.model,
            prompt_tokens=_int(self.usage.get("prompt_tokens")),
            completion_tokens=_int(self.usage.get("completion_tokens")),
            reasoning_tokens=_chat_reasoning(self.usage),
        )


def parse_chat_sse(raw: str) -> CompletionResult:
    state = _ChatStream()
    for line in raw.splitlines():
        payload = _json_data(line)
        if payload is None:
            continue
        state.feed(payload)
    return state.result()


@dataclass
class _ResponsesStream:
    """Общий разбор Responses SSE. Тело целиком не копится."""

    parts: list[str] = field(default_factory=list)
    done_text: str = ""
    model: str = ""
    finish: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    completed: dict[str, Any] | None = None

    def feed(self, payload: dict[str, Any]) -> str | None:
        etype = str(payload.get("type") or "")
        if etype == "response.output_text.delta" and payload.get("delta"):
            delta = str(payload["delta"])
            self.parts.append(delta)
            return delta
        if etype == "response.output_text.done":
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                self.done_text = text
            return None
        if etype == "response.completed" and isinstance(payload.get("response"), dict):
            self.completed = payload["response"]
            self.model = str(self.completed.get("model") or self.model)
            self.finish = str(self.completed.get("status") or "completed")
            if isinstance(self.completed.get("usage"), dict):
                self.usage = self.completed["usage"]
        return None

    def result(self) -> CompletionResult:
        text = "".join(self.parts)
        if len(self.done_text) > len(text):
            text = self.done_text
        parsed: CompletionResult | None = None
        if self.completed is not None:
            try:
                parsed = parse_responses_payload(self.completed)
            except ProviderError:
                parsed = None
        if parsed is not None and len(parsed.text) > len(text):
            text = parsed.text
        if not text:
            raise ProviderError("empty responses stream", retryable=True)
        if parsed is not None:
            return CompletionResult(
                text=text,
                finish_reason=parsed.finish_reason or self.finish or "completed",
                model=parsed.model or self.model,
                prompt_tokens=parsed.prompt_tokens,
                completion_tokens=parsed.completion_tokens,
                reasoning_tokens=parsed.reasoning_tokens,
            )
        details = self.usage.get("output_tokens_details") or {}
        return CompletionResult(
            text=text,
            finish_reason=self.finish or "completed",
            model=self.model,
            prompt_tokens=_int(self.usage.get("input_tokens")),
            completion_tokens=_int(self.usage.get("output_tokens")),
            reasoning_tokens=_int(details.get("reasoning_tokens")),
        )


def parse_responses_sse(raw: str) -> CompletionResult:
    """Собрать текст из SSE Responses API (``response.output_text.delta``)."""
    state = _ResponsesStream()
    for line in raw.splitlines():
        payload = _json_data(line)
        if payload is None:
            continue
        state.feed(payload)
    return state.result()


async def _emit_delta(callback: Any, delta: str) -> None:
    if callback is None or not delta:
        return
    result = callback(delta)
    if inspect.isawaitable(result):
        await result


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
        state = _ChatStream(model=request.model)
        async with client.stream("POST", url, headers=headers, json=body) as response:
            if response.status_code >= 400:
                raise ProviderError(
                    f"http {response.status_code}",
                    retryable=response.status_code in _RETRY_STATUS,
                    status_code=response.status_code,
                )
            async for line in response.aiter_lines():
                payload = _json_data(line)
                if payload is None:
                    continue
                for delta in state.feed(payload):
                    await _emit_delta(request.on_delta, delta)
        return state.result()

    async def _stream_responses(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        request: ProviderRequest,
    ) -> CompletionResult:
        """Kie: только stream. Нестриминговый reasoning часто отвечает 524.

        Usage и finish копятся по событию. Полный текст SSE в память не кладётся.
        """
        state = _ResponsesStream(model=request.model)
        async with client.stream("POST", url, headers=headers, json=body) as response:
            if response.status_code >= 400:
                raise ProviderError(
                    f"http {response.status_code}",
                    retryable=response.status_code in _RETRY_STATUS,
                    status_code=response.status_code,
                )
            async for line in response.aiter_lines():
                payload = _json_data(line)
                if payload is None:
                    continue
                delta = state.feed(payload)
                if delta:
                    await _emit_delta(request.on_delta, delta)
        result = state.result()
        if not result.model:
            result.model = request.model
        return result
