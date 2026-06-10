"""REST: компонентные промты (blocks / styles / compose)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.services.prompt_composer import (
    compose_for_node_type,
    compose_step,
    list_block_categories,
    list_step_templates,
    list_style_presets,
    load_style_preset,
    merge_project_prompt_config,
    NODE_TYPE_TO_STEP,
)
from app.services.prompt_library import (
    DEFAULT_NAME,
    is_valid_prompt_name,
    list_prompts as list_variants,
    write_prompt,
)
from app.services import gpt_text_builder as gtb
from app.web.deps import get_session
from sqlalchemy import select

router = APIRouter(prefix="/prompt-studio", tags=["prompt-studio"])


class ComposeRequest(BaseModel):
    node_type: str | None = None
    step_id: str | None = None
    project_id: int | None = None
    blocks: dict[str, str] | None = None
    vars: dict[str, Any] | None = None
    style_preset: str | None = None


class PromptOverridesPatch(BaseModel):
    style_profile: str | None = None
    blocks: dict[str, str] | None = None
    vars: dict[str, Any] | None = None
    use_blocks_v2: bool | None = None
    # legacy string overrides сохраняем
    legacy: dict[str, str] = Field(default_factory=dict)


class GptTextPatch(BaseModel):
    text: str = ""


class GptTextSaveTemplatePayload(BaseModel):
    name: str
    text: str | None = None


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
        text = gtb.get_display_text(project, step_code, **ctx) if supported else ""
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
        "text": gtb.get_display_text(project, step_code, **ctx),
        "supported": True,
        "is_override": gtb.has_override(project, step_code),
    }


@router.post("/projects/{project_id}/gpt-text/{step_code}/save-template")
async def save_gpt_text_as_template(
    project_id: int,
    step_code: str,
    payload: GptTextSaveTemplatePayload,
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    """Сохраняет текущий GPT-сопроводительный текст как шаблон prompts/<step>/<name>.md."""
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if not gtb.is_supported(step_code):
        raise HTTPException(status_code=400, detail=f"step {step_code} has no gpt text")

    raw_name = (payload.name or "").strip()
    if raw_name in ("", DEFAULT_NAME):
        raise HTTPException(
            status_code=400,
            detail="укажите имя шаблона (не «default»)",
        )
    if not is_valid_prompt_name(raw_name):
        raise HTTPException(status_code=400, detail=f"invalid template name: {raw_name!r}")

    if payload.text is not None and payload.text.strip():
        content = payload.text.strip()
    else:
        ctx = await _gpt_text_context(session, project, step_code)
        try:
            content = gtb.get_effective_text(project, step_code, **ctx)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    if not content.strip():
        raise HTTPException(status_code=400, detail="текст пустой — нечего сохранять")

    path = write_prompt(step_code, raw_name, content)
    return {
        "step_code": step_code,
        "name": raw_name,
        "filename": path.name,
        "path": str(path),
        "size": path.stat().st_size,
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
    text = gtb.get_display_text(project, step_code, **ctx) if supported else ""
    return {
        "step_code": step_code,
        "text": text,
        "supported": supported,
        "is_override": False,
    }


@router.get("/catalog")
async def get_catalog() -> dict[str, Any]:
    return {
        "block_categories": list_block_categories(),
        "steps": list_step_templates(),
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
) -> dict[str, str]:
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

    from sqlalchemy.orm.attributes import flag_modified

    project.prompt_overrides = po
    flag_modified(project, "prompt_overrides")
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


class GptVerdictRunPayload(BaseModel):
    prompt: str | None = None


class GptVerdictTemplateSavePayload(BaseModel):
    name: str
    content: str


@router.get("/verdict-templates/{step_code}")
async def list_verdict_templates_route(step_code: str) -> dict[str, Any]:
    from app.services.gpt_verdict_review import VERDICT_STUDIO_STEPS, list_verdict_templates

    if step_code not in VERDICT_STUDIO_STEPS:
        raise HTTPException(status_code=400, detail=f"no verdict check for {step_code}")
    return {"step_code": step_code, "templates": list_verdict_templates(step_code)}


@router.post("/verdict-templates/{step_code}")
async def save_verdict_template_route(
    step_code: str,
    payload: GptVerdictTemplateSavePayload,
) -> dict[str, Any]:
    from app.services.gpt_verdict_review import VERDICT_STUDIO_STEPS, save_verdict_template

    if step_code not in VERDICT_STUDIO_STEPS:
        raise HTTPException(status_code=400, detail=f"no verdict check for {step_code}")
    try:
        path = save_verdict_template(step_code, payload.name.strip(), payload.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "name": payload.name.strip(), "path": str(path.name)}


@router.get("/projects/{project_id}/step-attachments/{step_code}")
async def get_step_attachments(
    project_id: int,
    step_code: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.services.gpt_verdict_review import attachments_for_step

    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    files = await attachments_for_step(session, project, step_code)
    return {"step_code": step_code, "files": [p.name for p in files]}


@router.get("/projects/{project_id}/gpt-verdict/{step_code}")
async def get_gpt_verdict_context(
    project_id: int,
    step_code: str,
    template: str = "default",
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.services.gpt_verdict_review import (
        VERDICT_STUDIO_STEPS,
        artifact_text_for_step,
        attachments_for_step,
        list_verdict_templates,
        load_verdict_check_prompt,
    )

    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if step_code not in VERDICT_STUDIO_STEPS:
        raise HTTPException(status_code=400, detail=f"no verdict check for {step_code}")
    files = await attachments_for_step(session, project, step_code)
    tpl = (template or "default").strip() or "default"
    from app.services.gpt_verdict_review import verdict_template_for_project

    if tpl == "default":
        tpl = verdict_template_for_project(project, step_code)
    return {
        "step_code": step_code,
        "supported": True,
        "template": tpl,
        "templates": list_verdict_templates(step_code),
        "prompt": load_verdict_check_prompt(step_code, template=tpl),
        "artifact_preview": artifact_text_for_step(project, step_code)[:4000],
        "attachments": [str(p.name) for p in files],
    }


@router.post("/projects/{project_id}/gpt-verdict/{step_code}/save-template")
async def save_gpt_verdict_as_template(
    project_id: int,
    step_code: str,
    payload: GptVerdictTemplateSavePayload,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Сохраняет промт GPT-проверки в prompts/check_<step>/<name>.md."""
    from app.services.gpt_verdict_review import VERDICT_STUDIO_STEPS, save_verdict_template

    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if step_code not in VERDICT_STUDIO_STEPS:
        raise HTTPException(status_code=400, detail=f"no verdict check for {step_code}")

    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="укажите имя шаблона")
    if not payload.content.strip():
        raise HTTPException(status_code=400, detail="промт проверки пуст")
    try:
        path = save_verdict_template(step_code, name, payload.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"ok": True, "step_code": step_code, "name": name, "path": path.name}


@router.delete("/projects/{project_id}/gpt-verdict/{step_code}/templates/{name}")
async def delete_gpt_verdict_template(
    project_id: int,
    step_code: str,
    name: str,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.services.gpt_verdict_review import VERDICT_STUDIO_STEPS, delete_verdict_template

    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if step_code not in VERDICT_STUDIO_STEPS:
        raise HTTPException(status_code=400, detail=f"no verdict check for {step_code}")
    raw_name = name.strip()
    try:
        removed = delete_verdict_template(step_code, raw_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not removed:
        raise HTTPException(status_code=404, detail="template not found")
    return {"ok": True, "step_code": step_code, "name": raw_name, "removed": True}


@router.delete("/verdict-templates/{step_code}/{name}")
async def delete_verdict_template_route(step_code: str, name: str) -> dict[str, Any]:
    from app.services.gpt_verdict_review import VERDICT_STUDIO_STEPS, delete_verdict_template

    if step_code not in VERDICT_STUDIO_STEPS:
        raise HTTPException(status_code=400, detail=f"no verdict check for {step_code}")
    raw_name = name.strip()
    try:
        removed = delete_verdict_template(step_code, raw_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not removed:
        raise HTTPException(status_code=404, detail="template not found")
    return {"ok": True, "step_code": step_code, "name": raw_name, "removed": True}


@router.post("/projects/{project_id}/gpt-verdict/{step_code}/run")
async def run_gpt_verdict(
    project_id: int,
    step_code: str,
    payload: GptVerdictRunPayload | None = None,
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    from app.bots.browser import browser_session
    from app.bots.chatgpt import ChatGPTBot
    from app.services.gpt_verdict_review import VERDICT_STUDIO_STEPS, run_verdict_review
    from app.services.step_cancel import StepCancelledError, consume_stop

    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if step_code not in VERDICT_STUDIO_STEPS:
        raise HTTPException(status_code=400, detail=f"no verdict check for {step_code}")

    consume_stop(project_id)
    user_prompt = payload.prompt if payload else None
    try:
        async with browser_session() as bs:
            meta = project.meta if isinstance(project.meta, dict) else {}
            if meta.get("ai_new_window_per_check"):
                bs.force_new_window = True
            gpt = ChatGPTBot(bs)
            result = await run_verdict_review(
                session,
                project,
                step_code,
                gpt,
                user_prompt=user_prompt,
            )
    except StepCancelledError as e:
        raise HTTPException(status_code=499, detail=str(e)) from e

    from app.orchestrator.auto_advance import advance_after_gpt_verdict

    advanced = await advance_after_gpt_verdict(
        session,
        project,
        step_code,
        approved=result.approved,
        fix_applied=result.fix_applied,
    )
    if advanced:
        await session.commit()
        await session.refresh(project)

    return {
        "approved": result.approved,
        "fix_applied": result.fix_applied,
        "fix_path": result.fix_path,
        "advanced": advanced,
        "status": project.status.value,
        "rounds": result.rounds,
        "fix_text": result.fix_text,
        "last_raw": result.last_raw[:8000],
        "history": result.history,
    }

