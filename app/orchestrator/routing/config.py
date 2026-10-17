"""Загрузка ``pay52_routes.yaml``. Флаги D1/D6 по умолчанию не подтверждены."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_TRUE = {"1", "true", "yes", "on"}


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in _TRUE


@dataclass(frozen=True)
class Price:
    prompt: float
    completion: float
    reasoning: float


@dataclass(frozen=True)
class Hop:
    model: str
    provider: str
    reasoning_effort: str | None
    tier: str
    delegate: str | None = None


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    api_key_env: str
    base_url_env: str
    chat_path: str
    api: str
    alt_api_key_env: str = ""
    default_base_url: str = ""
    relay_token_env: str = ""


@dataclass(frozen=True)
class RouteConfig:
    d1_confirmed: bool
    d6_confirmed: bool
    fast_timeout_s: float
    strong_timeout_s: float
    retries: int
    backoff_base_s: float
    internal_budget_usd: float
    pipeline_budget_usd: float
    classifier_threshold: float
    classifier_temperature: float
    classifier_max_tokens: int
    fast_chain: tuple[Hop, ...]
    strong_unconfirmed: tuple[Hop, ...]
    strong_confirmed: tuple[Hop, ...]
    prices: dict[str, Price] = field(default_factory=dict)
    providers: dict[str, ProviderConfig] = field(default_factory=dict)


def default_config_path() -> Path:
    return Path(__file__).with_name("pay52_routes.yaml")


def _effort(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"", "null", "none", "off"}:
        return None
    return text


def _hop(raw: dict[str, Any], tier: str) -> Hop:
    delegate = raw.get("delegate")
    if delegate:
        return Hop(
            model="",
            provider="",
            reasoning_effort=None,
            tier=tier,
            delegate=str(delegate),
        )
    provider = str(raw["provider"])
    if provider not in {"52pay", "kie"}:
        raise ValueError(f"provider must be 52pay|kie, got {provider}")
    return Hop(
        model=str(raw["model"]),
        provider=provider,
        reasoning_effort=_effort(raw.get("reasoning_effort")),
        tier=tier,
    )


def _chain(raw: list[dict[str, Any]], tier: str) -> tuple[Hop, ...]:
    return tuple(_hop(item, tier) for item in raw)


def _prices(raw: dict[str, Any]) -> dict[str, Price]:
    prices: dict[str, Price] = {}
    for model, item in (raw or {}).items():
        prices[str(model)] = Price(
            prompt=float(item["prompt"]),
            completion=float(item["completion"]),
            reasoning=float(item.get("reasoning", item["completion"])),
        )
    if "default" not in prices:
        prices["default"] = Price(prompt=1.0, completion=4.0, reasoning=4.0)
    return prices


def _providers(raw: dict[str, Any]) -> dict[str, ProviderConfig]:
    out: dict[str, ProviderConfig] = {}
    for name, item in (raw or {}).items():
        out[str(name)] = ProviderConfig(
            name=str(name),
            api_key_env=str(item.get("api_key_env") or ""),
            base_url_env=str(item.get("base_url_env") or ""),
            chat_path=str(item.get("chat_path") or "/v1/chat/completions"),
            api=str(item.get("api") or "openai-chat"),
            alt_api_key_env=str(item.get("alt_api_key_env") or ""),
            default_base_url=str(item.get("default_base_url") or ""),
            relay_token_env=str(item.get("relay_token_env") or ""),
        )
    return out


def load_config(path: Path | None = None, *, apply_env: bool = True) -> RouteConfig:
    config_path = path or default_config_path()
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    flags = data.get("flags") or {}
    timeouts = data.get("timeouts_s") or {}
    spend = data.get("spend") or {}
    classifier = data.get("classifier") or {}
    chains = data.get("chains") or {}
    d1 = _as_bool(flags.get("d1_confirmed", False))
    d6 = _as_bool(flags.get("d6_confirmed", False))
    if apply_env:
        if "ORCH_D1_CONFIRMED" in os.environ:
            d1 = _as_bool(os.environ.get("ORCH_D1_CONFIRMED"))
        if "ORCH_D6_CONFIRMED" in os.environ:
            d6 = _as_bool(os.environ.get("ORCH_D6_CONFIRMED"))
    return RouteConfig(
        d1_confirmed=d1,
        d6_confirmed=d6,
        fast_timeout_s=float(timeouts.get("fast", 60)),
        strong_timeout_s=float(timeouts.get("strong", 300)),
        retries=int(data.get("retries", 2)),
        backoff_base_s=float(data.get("backoff_base_s", 1.0)),
        internal_budget_usd=float(spend.get("internal_sub_budget_usd", 10)),
        pipeline_budget_usd=float(spend.get("pipeline_sub_budget_usd", 40)),
        classifier_threshold=float(classifier.get("confidence_threshold", 0.8)),
        classifier_temperature=float(classifier.get("temperature", 0)),
        classifier_max_tokens=int(classifier.get("max_tokens", 120)),
        fast_chain=_chain(chains.get("fast") or [], "fast"),
        strong_unconfirmed=_chain(chains.get("strong_unconfirmed") or [], "strong"),
        strong_confirmed=_chain(chains.get("strong_confirmed") or [], "strong"),
        prices=_prices(data.get("prices_usd_per_million") or {}),
        providers=_providers(data.get("providers") or {}),
    )


def effective_tier(config: RouteConfig, requested: str) -> str:
    """Фактический tier. Пока D6 не подтверждён, fast поднимается в strong."""
    if requested not in {"fast", "strong"}:
        raise ValueError(f"tier must be fast|strong, got {requested!r}")
    if requested == "fast" and not config.d6_confirmed:
        return "strong"
    return requested


def strong_chain(config: RouteConfig) -> tuple[Hop, ...]:
    if config.d1_confirmed:
        return config.strong_confirmed
    return config.strong_unconfirmed


def expand_hops(config: RouteConfig, tier: str) -> list[Hop]:
    """Цепочка без delegate-ссылок. fast заканчивается strong-цепочкой."""
    if tier == "strong":
        return [hop for hop in strong_chain(config) if not hop.delegate]
    hops: list[Hop] = []
    for hop in config.fast_chain:
        if hop.delegate == "strong":
            hops.extend(expand_hops(config, "strong"))
        elif not hop.delegate:
            hops.append(hop)
    return hops
