"""Хоп ScriptedRouter берёт reasoning_effort из конфига, не из вызова.

Пакета ``app.orchestrator.routing`` в этом дереве нет. Фейк повторяет
сигнатуры WS5 @ ``111f55ee``: ``call`` без ``reasoning_effort``, фактический
tier — ``LLMResponse.tier`` (его заполняет ``effective_tier``). Платность
действия остаётся в рантайме, пока WS2 не добавит ``paid`` в R2.
"""

from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass

import pytest

from app.orchestrator.contracts import LLMResponse, LLMUsage


@dataclass(frozen=True)
class _Hop:
    model: str
    provider: str
    reasoning_effort: str | None
    tier: str
    delegate: str | None = None


@dataclass(frozen=True)
class _RouteConfig:
    d1_confirmed: bool
    d6_confirmed: bool
    fast_chain: tuple[_Hop, ...]
    strong_unconfirmed: tuple[_Hop, ...]
    strong_confirmed: tuple[_Hop, ...]


def _effective_tier(config: _RouteConfig, requested: str) -> str:
    """Пока D6 не подтверждён, fast поднимается в strong."""
    if requested not in {"fast", "strong"}:
        raise ValueError(f"tier must be fast|strong, got {requested!r}")
    if requested == "fast" and not config.d6_confirmed:
        return "strong"
    return requested


def _strong_chain(config: _RouteConfig) -> tuple[_Hop, ...]:
    if config.d1_confirmed:
        return config.strong_confirmed
    return config.strong_unconfirmed


def _expand_hops(config: _RouteConfig, tier: str) -> list[_Hop]:
    if tier == "strong":
        return [hop for hop in _strong_chain(config) if not hop.delegate]
    hops: list[_Hop] = []
    for hop in config.fast_chain:
        if hop.delegate == "strong":
            hops.extend(_expand_hops(config, "strong"))
        elif not hop.delegate:
            hops.append(hop)
    return hops


class _FakeScriptedRouter:
    """``call`` как у ModelRouter #359: усилия на вызове нет."""

    def __init__(self, config: _RouteConfig, *, scripts: list[dict] | None = None) -> None:
        self.config = config
        self.scripts = list(scripts or [])
        self.sent_hops: list[_Hop] = []

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
        on_delta: object = None,
    ) -> LLMResponse:
        del stream, temperature, max_tokens, agent_role, risk, mutating, budget_scope, on_delta
        if not messages:
            raise ValueError("messages is empty")
        if not run_id:
            raise ValueError("budget requires run_id")
        requested = (tier or "auto").strip().lower()
        if requested == "auto":
            requested = "strong"
        resolved = _effective_tier(self.config, requested)
        hops = _expand_hops(self.config, resolved)
        if not hops:
            raise RuntimeError(f"empty chain for {resolved}")
        hop = hops[0]
        self.sent_hops.append(hop)
        return LLMResponse(
            text="ok",
            call_id="scripted",
            model=hop.model,
            provider=hop.provider,
            tier=resolved,
            finish_reason="stop",
            usage=LLMUsage(),
            cost_usd=0.0,
            latency_ms=0,
            fallback_index=0,
            deterministic=True,
            fixture_key="scripted",
        )


def _router_types() -> tuple[type, type, type]:
    """Живой пакет WS5, если он уже в дереве. Иначе фейк с теми же сигнатурами."""
    try:
        routing = importlib.import_module("app.orchestrator.routing")
        config = importlib.import_module("app.orchestrator.routing.config")
    except ImportError:
        return _FakeScriptedRouter, _RouteConfig, _Hop
    return routing.ScriptedRouter, config.RouteConfig, config.Hop


def _config(route_config: type, hop_type: type):
    strong = hop_type(
        model="gpt-5.6-sol",
        provider="52pay",
        reasoning_effort="high",
        tier="strong",
    )
    fast = hop_type(
        model="glm-5.3-flash",
        provider="52pay",
        reasoning_effort=None,
        tier="fast",
        delegate="strong",
    )
    if route_config is _RouteConfig:
        return route_config(
            d1_confirmed=False,
            d6_confirmed=False,
            fast_chain=(fast,),
            strong_unconfirmed=(strong,),
            strong_confirmed=(strong,),
        )
    return route_config(
        d1_confirmed=False,
        d6_confirmed=False,
        fast_timeout_s=60,
        strong_timeout_s=300,
        retries=0,
        backoff_base_s=0,
        internal_budget_usd=10,
        pipeline_budget_usd=40,
        classifier_threshold=0.8,
        classifier_temperature=0,
        classifier_max_tokens=120,
        fast_chain=(fast,),
        strong_unconfirmed=(strong,),
        strong_confirmed=(strong,),
    )


@pytest.mark.asyncio
async def test_scripted_router_hop_gets_reasoning_effort_from_config() -> None:
    router_type, route_config, hop_type = _router_types()
    assert "reasoning_effort" not in inspect.signature(router_type.call).parameters
    router = router_type(_config(route_config, hop_type), scripts=[{"contains": "цель", "text": "ok"}])
    with pytest.raises(TypeError):
        await router.call(
            "fast",
            [{"role": "user", "content": "цель"}],
            run_id="run-1",
            task_id="1",
            reasoning_effort="low",
        )
    response = await router.call(
        "fast",
        [{"role": "user", "content": "цель"}],
        run_id="run-1",
        task_id="1",
    )
    sent = router.sent_hops
    assert sent, "хоп не записан"
    assert sent[0].reasoning_effort == "high"
    actual = getattr(response, "effective_tier", None) or response.tier
    assert actual == "strong"
    assert actual == response.tier
