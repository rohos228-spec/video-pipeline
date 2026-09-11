"""Помощник генерации: LLM-агент промптов с обвязкой-валидатором."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.gen_assistant import MAX_COUNT, MIN_COUNT, generate_prompts
from app.services.style_analyzer import analyze_style_images, categorize_styles

router = APIRouter(prefix="/gen-assistant", tags=["gen-assistant"])


class GenAssistantPromptsBody(BaseModel):
    request: str = ""
    agent_text: str = ""
    aspect: str = "9:16"
    count: int = Field(1, ge=MIN_COUNT, le=MAX_COUNT)


@router.post("/prompts")
async def post_gen_assistant_prompts(body: GenAssistantPromptsBody) -> dict[str, Any]:
    try:
        return await generate_prompts(
            request=body.request,
            agent_text=body.agent_text,
            aspect=body.aspect,
            count=body.count,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class AnalyzeStyleBody(BaseModel):
    paths: list[str] = Field(..., description="Локальные пути к референсным изображениям")
    name_hint: str | None = None


@router.post("/analyze-style")
async def post_analyze_style(body: AnalyzeStyleBody) -> dict[str, Any]:
    """Референсы → запись стиля (name/desc/category/prompt_core)."""
    try:
        return await analyze_style_images(body.paths, name_hint=body.name_hint)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class CategorizeStylesBody(BaseModel):
    entries: list[dict[str, Any]] = Field(..., description="Записи стилей из analyze-style")


@router.post("/categorize-styles")
async def post_categorize_styles(body: CategorizeStylesBody) -> dict[str, Any]:
    """Список стилей → категории по общим критериям."""
    try:
        return await categorize_styles(body.entries)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
