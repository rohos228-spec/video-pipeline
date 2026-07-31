"""Каталог текстовых LLM: GPT (kie) по умолчанию, Kimi K3 — доп. модель.

Выбор активной модели: data/text_llm_choice.json (Studio UI).
GPT_* в .env не затираются.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger

from app.settings import Settings, settings

_CHOICE_NAME = "text_llm_choice.json"

CATALOG: list[dict[str, str]] = [
    {
        "id": "gpt-kie",
        "provider": "kie",
        "label": "GPT (kie.ai)",
        "site": "kie.ai",
    },
    {
        "id": "kimi-k3-tokenrouter",
        "provider": "tokenrouter",
        "label": "Kimi K3 (TokenRouter)",
        "site": "tokenrouter.com",
    },
]


def _choice_path(cfg: Settings) -> Path:
    return Path(cfg.data_dir) / _CHOICE_NAME


def read_choice(cfg: Settings | None = None) -> dict[str, Any]:
    s = cfg or settings
    path = _choice_path(s)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:  # noqa: BLE001
        logger.warning("text_llm_choice: read failed: {}", e)
        return {}


def write_choice(
    *,
    provider: str,
    model_id: str | None = None,
    cfg: Settings | None = None,
) -> dict[str, Any]:
    s = cfg or settings
    provider = (provider or "kie").strip().lower()
    if provider in {"kimi", "kimi-k3", "moonshot"}:
        provider = "tokenrouter"
    if provider not in {"kie", "tokenrouter"}:
        raise ValueError(f"unknown text LLM provider: {provider!r}")
    payload = {
        "provider": provider,
        "model_id": model_id
        or ("kimi-k3-tokenrouter" if provider == "tokenrouter" else "gpt-kie"),
    }
    path = _choice_path(s)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.info(
        "text_llm_choice: active → {} ({})", payload["provider"], payload["model_id"]
    )
    return payload


def resolve_active_provider(cfg: Settings | None = None) -> str:
    """kie (GPT) по умолчанию; tokenrouter только по явному выбору."""
    s = cfg or settings
    raw_choice = str(read_choice(s).get("provider") or "").strip().lower()
    if raw_choice in {"tokenrouter", "kimi", "kimi-k3"}:
        return "tokenrouter"
    if raw_choice in {"kie", "gpt", "openai"}:
        return "kie"
    raw = (s.text_llm_provider or "kie").strip().lower()
    if raw in {"tokenrouter", "kimi", "kimi-k3", "kimi_k3", "moonshot"}:
        return "tokenrouter"
    return "kie"


def catalog_status(cfg: Settings | None = None) -> dict[str, Any]:
    s = cfg or settings
    active = resolve_active_provider(s)
    models: list[dict[str, Any]] = []
    for item in CATALOG:
        prov = item["provider"]
        if prov == "tokenrouter":
            model = s.tokenrouter_model
            key_ok = bool((s.tokenrouter_api_key or "").strip())
            base = s.tokenrouter_base_url
        else:
            model = s.gpt_model
            key_ok = bool(
                (s.gpt_api_key or "").strip() or (s.grsai_api_key or "").strip()
            )
            base = s.gpt_base_url or s.grsai_base_url
        models.append(
            {
                **item,
                "model": model,
                "base_url": base,
                "key_configured": key_ok,
                "active": prov == active,
            }
        )
    # label/model без рекурсии через property на «чужом» singleton
    if active == "tokenrouter":
        short = (s.tokenrouter_model or "kimi-k3").split("/")[-1]
        label = f"Kimi K3 · TokenRouter ({short})"
        active_model = s.tokenrouter_model
    else:
        label = f"GPT · kie.ai ({s.gpt_model})"
        active_model = s.gpt_model
    return {
        "active_provider": active,
        "active_label": label,
        "active_model": active_model,
        "models": models,
    }
