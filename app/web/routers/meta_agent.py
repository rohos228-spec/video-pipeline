"""Роутер Мета-Агента: компиляция и сохранение системных мастер-промптов."""

from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import commit_with_retry
from app.web.deps import get_session
from app.models import Project
from app.services.meta_prompt_compiler import compile_meta_prompt
from app.services.prompt_library import (
    STEP_FOLDERS,
    step_dir,
    touch_prompt_meta,
    _sanitize_name,
)

router = APIRouter(prefix="/meta-agent", tags=["meta-agent"])


class CompilePromptRequest(BaseModel):
    step_code: str = Field(..., description="Код шага/папки (excel_gpt, hero_style, items, img_pr, etc.)")
    user_intent: str = Field(..., description="Описание задачи на естественном языке")
    project_id: int | None = Field(default=None, description="ID проекта (для контекста темы)")
    target_name: str | None = Field(default=None, description="Желаемое имя файла/пресета")


class SaveAndActivateRequest(BaseModel):
    step_code: str = Field(..., description="Код шага/папки")
    name: str = Field(..., description="Имя файла без .md")
    content: str = Field(..., description="Тело промпта")
    project_id: int | None = Field(default=None, description="ID проекта для активации")
    activate: bool = Field(default=True, description="Активировать этот вариант в проекте")


@router.post("/compile")
async def compile_prompt_endpoint(
    req: CompilePromptRequest,
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Генерирует готовый системный промпт через LLM."""
    topic = ""
    if req.project_id:
        project = await db.get(Project, req.project_id)
        if project:
            topic = project.topic or project.title or ""

    try:
        result = await compile_meta_prompt(
            step_code=req.step_code,
            user_intent=req.user_intent,
            project_topic=topic,
            target_name=req.target_name or "",
        )
        return {"ok": True, **result}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ошибка генерации промпта: {exc}")


@router.post("/save-and-activate")
async def save_and_activate_endpoint(
    req: SaveAndActivateRequest,
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Сохраняет скомпилированный .md файл и при необходимости активирует его."""
    clean_code = (req.step_code or "").strip().lower()
    if clean_code not in STEP_FOLDERS:
        raise HTTPException(status_code=400, detail=f"Неизвестный step_code: {clean_code}")

    clean_name = _sanitize_name(req.name or "custom_agent_prompt")
    if not clean_name:
        clean_name = "custom_agent_prompt"

    folder = step_dir(clean_code)
    target_file = folder / f"{clean_name}.md"

    # Запись на диск
    target_file.write_text(req.content, encoding="utf-8")
    touch_prompt_meta(clean_code, clean_name, len(req.content.encode("utf-8")))

    activated = False
    if req.activate and req.project_id:
        project = await db.get(Project, req.project_id)
        if project:
            overrides = dict(project.prompt_overrides or {})
            overrides[clean_code] = clean_name
            project.prompt_overrides = overrides
            await commit_with_retry(db)
            activated = True

    return {
        "ok": True,
        "step_code": clean_code,
        "name": clean_name,
        "file_name": f"{clean_name}.md",
        "file_path": str(target_file),
        "activated": activated,
    }