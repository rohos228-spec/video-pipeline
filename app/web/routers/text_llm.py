"""Выбор текстовой LLM: GPT (kie) + доп. Kimi K3 (TokenRouter)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.text_llm_catalog import catalog_status, write_choice
from app.services.vibecode_catalog import grouped_catalog, markup_for_channel

router = APIRouter(prefix="/text-llm", tags=["text-llm"])


class TextLlmSelectBody(BaseModel):
    provider: str = Field(..., description="kie | vibecode | tokenrouter")
    model_id: str | None = None


@router.get("")
async def get_text_llm(channel: str = "cheap") -> dict[str, Any]:
    status = catalog_status()
    status["catalog"] = grouped_catalog(channel=channel)
    status["catalog_channels"] = {
        "cheap": grouped_catalog(channel="cheap"),
        "stable": grouped_catalog(channel="stable"),
    }
    return status


@router.get("/catalog")
async def get_text_llm_catalog(channel: str = "cheap") -> dict[str, Any]:
    ch = channel if channel in {"cheap", "stable"} else "cheap"
    payload = grouped_catalog(channel=ch)
    payload["markup"] = markup_for_channel(ch)
    return payload


@router.put("")
async def select_text_llm(body: TextLlmSelectBody) -> dict[str, Any]:
    try:
        write_choice(provider=body.provider, model_id=body.model_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return catalog_status() | {
        "catalog": grouped_catalog(channel="cheap"),
        "catalog_channels": {
            "cheap": grouped_catalog(channel="cheap"),
            "stable": grouped_catalog(channel="stable"),
        },
    }
