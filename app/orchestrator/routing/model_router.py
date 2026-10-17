"""Model router: ``call`` / ``classify`` / ``call_batch``.

Три реализации одного интерфейса: ``LiveRouter``, ``ReplayRouter``,
``ScriptedRouter``. Живой HTTP — только у Live. Replay при промахе фикстуры
бросает ``FixtureMiss`` и не зовёт провайдера.

Пока D1 не подтверждён, strong-primary = ``gpt-5.6-sol`` + ``reasoning_effort=high``,
затем Kie. Пока D6 не подтверждён, fast-tier выключен: запрошенный fast
исполняется strong high. ``classify().tier`` при этом остаётся логическим
(его считает E6); фактический tier — ``effective_tier`` и ``LLMResponse.tier``.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.orchestrator.routing.classifier import (
    classifier_messages,
    classify_rules,
    parse_classifier_output,
)
from app.orchestrator.routing.config import (
    Hop,
    RouteConfig,
    effective_tier,
    expand_hops,
    load_config,
)
from app.orchestrator.routing.hashing import fixture_key, messages_sha256, normalize_messages
from app.orchestrator.routing.ledger import SpendLedger, get_spend_ledger
from app.orchestrator.routing.sink import LlmCallSink, NullLlmCallSink
from app.orchestrator.routing.transport import (
    CompletionResult,
    HttpxTransport,
    ProviderError,
    ProviderRequest,
    ProviderTimeout,
)
from app.orchestrator.routing.types import (
    ApprovalEvent,
    CallContext,
    Classification,
    FixtureMiss,
    LlmCallRecord,
    LLMResponse,
    LLMUsage,
    RouterError,
    RouterExhausted,
    budget_approval,
    coerce_mode,
    waiting_approval_status,
)
from app.orchestrator.routing.wire import kie_model_slug

_ROLES = {"supervisor", "planner", "critic", "executor", "classifier", "summarizer"}
_SCOPES = {"internal", "pipeline"}

__all__ = [
    "LiveRouter",
    "ModelRouter",
    "RouterError",
    "RouterExhausted",
    "build_router",
    "compute_cost",
    "primary_key_fields",
]


_PRICE_ALIAS = {
    "gpt-5-6-sol": "gpt-5.6-sol",
    "claude-haiku-4-5": "claude-haiku",
    "claude-haiku-4-5-20251001": "claude-haiku",
}


def compute_cost(model: str, usage: LLMUsage, prices: dict) -> float:
    price = prices.get(model) or prices.get(_PRICE_ALIAS.get(model, "")) or prices.get("default")
    if price is None:
        return 0.0
    return (
        usage.prompt_tokens * price.prompt
        + usage.completion_tokens * price.completion
        + usage.reasoning_tokens * price.reasoning
    ) / 1_000_000


def primary_key_fields(
    config: RouteConfig,
    *,
    tier: str,
    messages: list,
    temperature: float | None,
    max_tokens: int | None,
) -> dict[str, Any]:
    """Поля ключа фикстуры для primary-попытки уже после D1/D6."""
    resolved = effective_tier(config, tier)
    hops = expand_hops(config, resolved)
    if not hops:
        raise RouterError(f"empty chain for tier {resolved}")
    return {
        "tier": resolved,
        "messages": normalize_messages(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
        "reasoning_effort": hops[0].reasoning_effort,
    }


def _role(value: str | None) -> str | None:
    if value is None or str(value).strip() == "":
        return None
    role = str(value).strip().lower()
    if role not in _ROLES:
        raise ValueError(f"agent_role must be one of {sorted(_ROLES)}")
    return role


def _last_user_text(messages: list[dict[str, str]]) -> str:
    for message in reversed(messages):
        if message.get("role") == "user":
            return message.get("content") or ""
    return messages[-1].get("content") or ""


def _latency_ms(started: float) -> int:
    return max(0, int(round((time.perf_counter() - started) * 1000)))


class ModelRouter:
    """Общая политика: классификация, бюджет, ретраи, stagger, лог попыток."""

    impl_name = "live"

    def __init__(
        self,
        config: RouteConfig | None = None,
        *,
        transport: Any = None,
        ledger: SpendLedger | None = None,
        sink: LlmCallSink | None = None,
        sleeper: Any = None,
        rng: random.Random | None = None,
        clock: Any = None,
    ) -> None:
        self.config = config or load_config()
        self.transport = transport
        self.ledger = ledger if ledger is not None else get_spend_ledger()
        self.sink = sink or NullLlmCallSink()
        self._sleep = sleeper or asyncio.sleep
        self._rng = rng or random.Random()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def call(
        self,
        tier: str = "auto",
        messages: list | None = None,
        stream: bool = False,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
        agent_role: str | None = None,
        risk: str | None = None,
        mutating: bool | None = None,
        budget_scope: str | None = None,
        on_delta: Any = None,
    ) -> LLMResponse:
        """``call(tier, messages, stream, *, run_id, task_id, agent_role, budget_scope)``.

        ``run_id``, ``task_id``, ``agent_role``, ``budget_scope`` — keyword-only,
        как ``ModelRouter`` ws3-r3. ``budget_scope=None`` значит ``internal``.
        """
        normalized = normalize_messages(messages)
        if not normalized:
            raise ValueError("messages is empty")
        scope = "internal" if budget_scope is None else budget_scope
        if scope not in _SCOPES:
            raise ValueError("budget_scope must be internal|pipeline")
        requested = (tier or "auto").strip().lower()
        role = _role(agent_role)
        if requested == "auto":
            decision = await self.classify(
                _last_user_text(normalized),
                agent_role=role,
                risk=risk,
                mutating=mutating,
                run_id=run_id,
                task_id=task_id,
            )
            logical = decision.tier
            resolved = decision.effective_tier
            deterministic = decision.deterministic
            confidence = None if deterministic else decision.confidence
        elif requested in {"fast", "strong"}:
            logical = requested
            resolved = effective_tier(self.config, requested)
            deterministic = True
            confidence = None
        else:
            raise ValueError("tier must be fast|strong|auto")
        ctx = CallContext(
            requested_tier=requested,
            logical_tier=logical,
            effective_tier=resolved,
            messages=normalized,
            stream=bool(stream),
            temperature=temperature,
            max_tokens=max_tokens,
            run_id=run_id,
            task_id=task_id,
            agent_role=role,
            budget_scope=scope,
            deterministic=deterministic,
            classifier_confidence=confidence,
            on_delta=on_delta,
            messages_sha256=messages_sha256(normalized),
        )
        return await self._dispatch(ctx)

    async def classify(
        self,
        message: str,
        *,
        agent_role: str | None = None,
        risk: str | None = None,
        mutating: bool | None = None,
        run_id: str | None = None,
        task_id: str | None = None,
    ) -> Classification:
        """Логический tier до флага D6. Спорное — вызов классификатора.

        Async: спорная ветка делает LLM-ход. Синхронный вызов не может
        дождаться фикстуры или 52pay.
        """
        role = _role(agent_role)
        hit = classify_rules(
            message or "",
            agent_role=role,
            risk=risk,
            mutating=mutating,
        )
        if hit.tier is not None:
            return Classification(
                tier=hit.tier,
                mode=coerce_mode(hit.mode, kill_switch=hit.kill_switch),
                deterministic=True,
                confidence=1.0,
                reason=hit.reason,
                effective_tier=effective_tier(self.config, hit.tier),
                kill_switch=hit.kill_switch,
            )
        response = await self.call(
            "fast",
            classifier_messages(message or ""),
            temperature=self.config.classifier_temperature,
            max_tokens=self.config.classifier_max_tokens,
            agent_role="classifier",
            run_id=run_id,
            task_id=task_id,
            budget_scope="internal",
        )
        parsed = parse_classifier_output(response.text)
        chosen = parsed.tier
        if parsed.confidence < self.config.classifier_threshold:
            chosen = "strong"
        mode = hit.mode if hit.mode_locked else parsed.mode
        return Classification(
            tier=chosen,
            mode=coerce_mode(mode, kill_switch=hit.kill_switch),
            deterministic=False,
            confidence=parsed.confidence,
            reason="classifier",
            effective_tier=effective_tier(self.config, chosen),
            kill_switch=hit.kill_switch,
        )

    async def call_batch(self, items: Sequence[dict]) -> list[LLMResponse]:
        """Параллельный батч. Старт i-го запроса сдвинут на i * gap, gap ∈ [2, 5]."""
        if not items:
            return []
        gap = float(self._rng.uniform(self.config.stagger_min_s, self.config.stagger_max_s))

        async def _one(index: int, spec: dict) -> LLMResponse:
            if index:
                await self._sleep(index * gap)
            payload = dict(spec)
            tier = payload.pop("tier", "auto")
            messages = payload.pop("messages")
            stream = payload.pop("stream", False)
            return await self.call(tier, messages, stream, **payload)

        return list(await asyncio.gather(*[_one(i, spec) for i, spec in enumerate(items)]))

    async def _dispatch(self, ctx: CallContext) -> LLMResponse:
        return await self._run_chain(ctx)

    def _hops(self, ctx: CallContext) -> list[Hop]:
        hops = expand_hops(self.config, ctx.effective_tier)
        if not hops:
            raise RouterExhausted(f"empty chain for {ctx.effective_tier}")
        return hops

    def _blocked(self, ctx: CallContext) -> bool:
        return ctx.budget_scope == "internal" and self.ledger.exhausted(ctx.run_id)

    async def _run_chain(self, ctx: CallContext) -> LLMResponse:
        hops = self._hops(ctx)
        if self._blocked(ctx):
            return self._waiting_approval(ctx, hops[0], 0)
        last_error = "chain exhausted"
        for fallback_index, hop in enumerate(hops):
            timeout_s = (
                self.config.fast_timeout_s if hop.tier == "fast" else self.config.strong_timeout_s
            )
            for attempt in range(1, self.config.retries + 2):
                if self._blocked(ctx):
                    return self._waiting_approval(ctx, hop, fallback_index)
                call_id = str(uuid.uuid4())
                started = time.perf_counter()
                try:
                    result = await self._provider_complete(hop, ctx, timeout_s)
                except FixtureMiss as exc:
                    self._log(
                        ctx,
                        hop,
                        call_id=call_id,
                        attempt=attempt,
                        fallback_index=fallback_index,
                        status="fixture_miss",
                        error="fixture-miss",
                        latency_ms=_latency_ms(started),
                        fixture_key=exc.fixture_key,
                    )
                    raise
                except ProviderTimeout as exc:
                    self._log(
                        ctx,
                        hop,
                        call_id=call_id,
                        attempt=attempt,
                        fallback_index=fallback_index,
                        status="timeout",
                        error=str(exc) or "timeout",
                        latency_ms=_latency_ms(started),
                    )
                    if attempt <= self.config.retries:
                        await self._sleep(self.config.backoff_base_s * (2 ** (attempt - 1)))
                        continue
                    last_error = str(exc) or "timeout"
                    break
                except ProviderError as exc:
                    self._log(
                        ctx,
                        hop,
                        call_id=call_id,
                        attempt=attempt,
                        fallback_index=fallback_index,
                        status="error",
                        error=str(exc) or "error",
                        latency_ms=_latency_ms(started),
                    )
                    if exc.retryable and attempt <= self.config.retries:
                        await self._sleep(self.config.backoff_base_s * (2 ** (attempt - 1)))
                        continue
                    last_error = str(exc) or "error"
                    break
                else:
                    return self._success(
                        ctx,
                        hop,
                        result,
                        call_id=call_id,
                        attempt=attempt,
                        fallback_index=fallback_index,
                        started=started,
                    )
        raise RouterExhausted(last_error)

    async def _provider_complete(
        self,
        hop: Hop,
        ctx: CallContext,
        timeout_s: float,
    ) -> CompletionResult:
        if self.transport is None:
            self.transport = HttpxTransport(self.config.providers)
        model = hop.model
        stream = bool(ctx.stream)
        if hop.provider == "kie":
            # Последний резерв. Нестриминговый reasoning на Kie часто отвечает 524.
            model = kie_model_slug()
            stream = True
        request = ProviderRequest(
            model=model,
            provider=hop.provider,
            messages=ctx.messages,
            temperature=ctx.temperature,
            max_tokens=ctx.max_tokens,
            reasoning_effort=hop.reasoning_effort,
            timeout_s=timeout_s,
            stream=stream,
            on_delta=ctx.on_delta,
        )
        return await self.transport.complete(request)

    def _success(
        self,
        ctx: CallContext,
        hop: Hop,
        result: CompletionResult,
        *,
        call_id: str,
        fallback_index: int,
        started: float,
        attempt: int = 1,
        fixture_key_value: str | None = None,
        cost_override: float | None = None,
        latency_override: int | None = None,
    ) -> LLMResponse:
        usage = LLMUsage(
            prompt_tokens=int(result.prompt_tokens),
            completion_tokens=int(result.completion_tokens),
            reasoning_tokens=int(result.reasoning_tokens),
        )
        model = result.model or hop.model
        cost = (
            float(cost_override)
            if cost_override is not None
            else compute_cost(model, usage, self.config.prices)
        )
        approval = self._charge(ctx, cost)
        latency = _latency_ms(started) if latency_override is None else int(latency_override)
        response = LLMResponse(
            text=result.text,
            call_id=call_id,
            model=model,
            provider=hop.provider,
            tier=ctx.effective_tier,
            finish_reason=result.finish_reason or "stop",
            usage=usage,
            cost_usd=cost,
            latency_ms=latency,
            fallback_index=fallback_index,
            deterministic=ctx.deterministic,
            fixture_key=fixture_key_value,
            task_status=waiting_approval_status() if approval is not None else None,
            approval=approval,
        )
        self._log(
            ctx,
            hop,
            call_id=call_id,
            attempt=attempt,
            fallback_index=fallback_index,
            status="ok",
            error=None,
            latency_ms=latency,
            usage=usage,
            cost_usd=cost,
            fixture_key=fixture_key_value,
            model=model,
        )
        return response

    def _charge(self, ctx: CallContext, cost: float) -> ApprovalEvent | None:
        result = self.ledger.charge(ctx.run_id, cost, scope=ctx.budget_scope)
        if ctx.budget_scope != "internal" or not result.over_budget:
            return None
        return self._approval(ctx, result.spent_usd)

    def _approval(self, ctx: CallContext, spent: float) -> ApprovalEvent:
        raw = ctx.task_id
        task_id: int | None
        task_ref: str | None = None
        if isinstance(raw, int) and not isinstance(raw, bool):
            task_id = raw
        elif raw is not None and str(raw).strip().isdigit():
            task_id = int(str(raw).strip())
        else:
            task_id = None
            if raw is not None and str(raw).strip() != "":
                task_ref = str(raw)
        return budget_approval(
            task_id=task_id,
            spent_usd=spent,
            budget_usd=self.ledger.internal_budget_usd,
            run_id=ctx.run_id,
            task_ref=task_ref,
        )

    def _waiting_approval(self, ctx: CallContext, hop: Hop, fallback_index: int) -> LLMResponse:
        """Бюджет уже исчерпан: модель не зовём, задачу не валим.

        Строку ``orch_llm_calls`` не пишем — попытки провайдера не было,
        а в enum статуса вызова нет отдельного значения. Сигнал супервизору:
        ``task_status=waiting_approval`` и ``ApprovalEvent.reason=budget_exhausted``.
        """
        return LLMResponse(
            text="",
            call_id=str(uuid.uuid4()),
            model=hop.model,
            provider=hop.provider,
            tier=ctx.effective_tier,
            finish_reason="",
            usage=LLMUsage(),
            cost_usd=0.0,
            latency_ms=0,
            fallback_index=fallback_index,
            deterministic=ctx.deterministic,
            fixture_key=None,
            task_status=waiting_approval_status(),
            approval=self._approval(ctx, self.ledger.spent(ctx.run_id)),
        )

    def _log(
        self,
        ctx: CallContext,
        hop: Hop,
        *,
        call_id: str,
        attempt: int,
        fallback_index: int,
        status: str,
        error: str | None,
        latency_ms: int,
        usage: LLMUsage | None = None,
        cost_usd: float = 0.0,
        fixture_key: str | None = None,
        model: str | None = None,
    ) -> None:
        tokens = usage or LLMUsage()
        self.sink.record(
            LlmCallRecord(
                call_id=call_id,
                task_id=ctx.task_id,
                run_id=ctx.run_id,
                agent_role=ctx.agent_role,
                requested_tier=ctx.requested_tier,
                tier=ctx.effective_tier,
                model=model or hop.model,
                provider=hop.provider,
                reasoning_effort=hop.reasoning_effort,
                fallback_index=fallback_index,
                attempt=attempt,
                status=status,
                error=error,
                deterministic_flag=ctx.deterministic,
                classifier_confidence=ctx.classifier_confidence,
                latency_ms=int(latency_ms),
                prompt_tokens=tokens.prompt_tokens,
                completion_tokens=tokens.completion_tokens,
                reasoning_tokens=tokens.reasoning_tokens,
                cost_usd=float(cost_usd),
                budget_scope=ctx.budget_scope,
                router_impl=self.impl_name,
                messages_sha256=ctx.messages_sha256,
                fixture_key=fixture_key,
                created_at=self._created_at(),
            )
        )

    def _created_at(self) -> str:
        moment = self._clock()
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class LiveRouter(ModelRouter):
    """52pay (``PAY52_GPT_API_KEY``) и резерв Kie. Ретраи — в базовом классе."""

    impl_name = "live"


def build_router(impl: str = "live", **kwargs: Any) -> ModelRouter:
    """``live`` | ``replay`` | ``scripted``."""
    kind = (impl or "live").strip().lower()
    if kind == "live":
        return LiveRouter(**kwargs)
    if kind == "replay":
        from app.orchestrator.routing.replay import ReplayRouter

        return ReplayRouter(**kwargs)
    if kind == "scripted":
        from app.orchestrator.routing.scripted import ScriptedRouter

        return ScriptedRouter(**kwargs)
    raise ValueError(f"unknown router impl {impl!r}")


def make_fixture_key(config: RouteConfig, ctx_tier: str, messages, temperature, max_tokens) -> str:
    fields = primary_key_fields(
        config,
        tier=ctx_tier,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return fixture_key(**fields)
