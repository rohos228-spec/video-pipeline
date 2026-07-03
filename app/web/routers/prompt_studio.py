"""REST: компонентные промты (blocks / styles / compose)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.services import gpt_text_builder as gtb
from app.services.prompt_composer import (
    NODE_TYPE_TO_STEP,
    compose_for_node_type,
    compose_step,
    list_block_categories,
    list_step_templates,
    list_style_presets,
    load_style_preset,
    merge_project_prompt_config,
    step_block_categories,
)
from app.services.prompt_library import list_prompts as list_variants
from app.web.deps import get_session

router = APIRouter(prefix="/prompt-studio", tags=["prompt-studio"])


class ComposeRequest(BaseModel):
    node_type: str | None = None
    step_id: str | None = None
    project_id: int | None = None
    # Значение категории: либо имя файла (str, legacy), либо
    # {"name"?: str, "text"?: str, "weight"?: float 0..1}.
    blocks: dict[str, Any] | None = None
    vars: dict[str, Any] | None = None
    style_preset: str | None = None


class PromptOverridesPatch(BaseModel):
    style_profile: str | None = None
    blocks: dict[str, Any] | None = None
    vars: dict[str, Any] | None = None
    use_blocks_v2: bool | None = None
    # legacy string overrides сохраняем
    legacy: dict[str, str] = Field(default_factory=dict)


class GptTextPatch(BaseModel):
    text: str = ""


async def _gpt_text_context(session: AsyncSession, project: Project, step_code: str) -> dict:
    ctx: dict = {}
    if step_code == "img_pr":
        frames = (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.number.asc())
            )
        ).scalars().all()
        if frames:
            ctx["voiceover_line"] = "-".join(
                (fr.voiceover_text or "").strip() for fr in frames
            )
            ctx["n_frames"] = len(frames)
    if step_code == "anim_pr":
        frames = (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.number.asc())
            )
        ).scalars().all()
        if frames:
            ctx["frames"] = frames
        ctx["prompt_file_name"] = "prompt_anim_pr.md"
    return ctx


@router.get("/projects/{project_id}/gpt-text/{step_code}")
async def get_project_gpt_text(
    project_id: int,
    step_code: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    supported = gtb.is_supported(step_code)
    ctx = await _gpt_text_context(session, project, step_code)
    try:
        text = gtb.get_effective_text(project, step_code, **ctx) if supported else ""
    except ValueError:
        text = ""
        supported = False
    return {
        "step_code": step_code,
        "text": text,
        "supported": supported,
        "is_override": gtb.has_override(project, step_code),
        "human_name": gtb.step_human_name(step_code),
    }


@router.put("/projects/{project_id}/gpt-text/{step_code}")
async def save_project_gpt_text(
    project_id: int,
    step_code: str,
    payload: GptTextPatch,
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if not gtb.is_supported(step_code):
        raise HTTPException(status_code=400, detail=f"step {step_code} has no gpt text")
    try:
        await gtb.set_override(session, project, step_code, payload.text)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await session.commit()
    ctx = await _gpt_text_context(session, project, step_code)
    return {
        "step_code": step_code,
        "text": gtb.get_effective_text(project, step_code, **ctx),
        "supported": True,
        "is_override": gtb.has_override(project, step_code),
    }


@router.delete("/projects/{project_id}/gpt-text/{step_code}")
async def reset_project_gpt_text(
    project_id: int,
    step_code: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    await gtb.clear_override(session, project, step_code)
    await session.commit()
    ctx = await _gpt_text_context(session, project, step_code)
    supported = gtb.is_supported(step_code)
    text = gtb.get_effective_text(project, step_code, **ctx) if supported else ""
    return {
        "step_code": step_code,
        "text": text,
        "supported": supported,
        "is_override": False,
    }


@router.get("/catalog")
async def get_catalog() -> dict[str, Any]:
    steps = list_step_templates()
    return {
        "block_categories": list_block_categories(),
        "steps": steps,
        # Какие {{BLOCK:cat}} реально встречаются в шаблоне каждого шага —
        # чтобы UI не предлагал редактировать категории, которые для этого
        # шага ни на что не влияют.
        "step_block_categories": {s: step_block_categories(s) for s in steps},
        "node_type_to_step": NODE_TYPE_TO_STEP,
        "style_presets": list_style_presets(),
    }


@router.get("/styles/{preset_id}")
async def get_style(preset_id: str) -> dict[str, Any]:
    try:
        return load_style_preset(preset_id)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/variants/{step_code}")
async def get_step_variants(step_code: str) -> list[str]:
    """Legacy .md варианты. Шаги без GPT-промта (img, video, audio…) → []."""
    try:
        return list_variants(step_code)
    except ValueError:
        return []
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.post("/compose")
async def compose_preview(
    payload: ComposeRequest,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    if payload.style_preset:
        overrides["style_profile"] = payload.style_preset
    if payload.blocks:
        overrides["blocks"] = payload.blocks
    if payload.vars:
        overrides["vars"] = payload.vars

    hero_description: str | None = None
    topic: str | None = None
    if payload.project_id is not None:
        project = await session.get(Project, payload.project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
        po = dict(project.prompt_overrides or {})
        po.update(overrides)
        overrides = po
        hero_description = (
            (project.hero_descriptions or [None])[0]
            if isinstance(project.hero_descriptions, list)
            else None
        )
        topic = project.topic

    blocks, vars_ = merge_project_prompt_config(
        overrides, hero_description=hero_description, topic=topic
    )
    if payload.blocks:
        blocks.update(payload.blocks)
    if payload.vars:
        vars_.update(payload.vars)

    try:
        if payload.step_id:
            text = compose_step(payload.step_id, blocks, vars_)
        elif payload.node_type:
            text = compose_for_node_type(
                payload.node_type,
                overrides,
                hero_description=hero_description,
                topic=topic,
            )
        else:
            raise HTTPException(status_code=400, detail="node_type or step_id required")
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return {"text": text, "blocks": blocks, "vars": {k: str(v) for k, v in vars_.items()}}


@router.patch("/projects/{project_id}/prompt-config")
async def patch_project_prompt_config(
    project_id: int,
    payload: PromptOverridesPatch,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    po = dict(project.prompt_overrides or {})
    if payload.style_profile is not None:
        po["style_profile"] = payload.style_profile
    if payload.blocks is not None:
        po["blocks"] = payload.blocks
        po["use_blocks_v2"] = True
    if payload.vars is not None:
        po["vars"] = payload.vars
    if payload.use_blocks_v2 is not None:
        po["use_blocks_v2"] = payload.use_blocks_v2
    for k, v in payload.legacy.items():
        po[k] = v

    project.prompt_overrides = po
    await session.commit()
    blocks, vars_ = merge_project_prompt_config(
        po,
        hero_description=(
            (project.hero_descriptions or [None])[0]
            if isinstance(project.hero_descriptions, list)
            else None
        ),
        topic=project.topic,
    )
    return {
        "prompt_overrides": po,
        "resolved_blocks": blocks,
        "resolved_vars": {k: str(v) for k, v in vars_.items()},
    }
