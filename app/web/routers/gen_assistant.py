"""Помощник генерации: LLM-агент промптов с обвязкой-валидатором."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from app.settings import settings
from app.services.gen_assistant import MAX_COUNT, MIN_COUNT, generate_prompts
from app.services.style_analyzer import (
    IMG_EXTS,
    MAX_IMAGES,
    analyze_style_images,
    build_style_agent,
    categorize_styles,
)

router = APIRouter(prefix="/gen-assistant", tags=["gen-assistant"])


class GenAssistantPromptsBody(BaseModel):
    request: str = ""
    agent_text: str = ""
    aspect: str = "9:16"
    count: int = Field(1, ge=MIN_COUNT, le=MAX_COUNT)
    ref_labels: list[str] = Field(default_factory=list)


@router.post("/prompts")
async def post_gen_assistant_prompts(body: GenAssistantPromptsBody) -> dict[str, Any]:
    logger.info(
        "gen-assistant POST request_chars={} agent_chars={} count={} aspect={} refs={}",
        len(body.request or ""),
        len(body.agent_text or ""),
        body.count,
        body.aspect,
        len(body.ref_labels or []),
    )
    try:
        return await generate_prompts(
            request=body.request,
            agent_text=body.agent_text,
            aspect=body.aspect,
            count=body.count,
            ref_labels=body.ref_labels,
        )
    except ValueError as e:
        logger.warning("gen-assistant rejected: {}", e)
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        logger.exception("gen-assistant failed")
        raise HTTPException(status_code=500, detail=f"Агент сломался: {e}") from e


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


@router.post("/build-agent")
async def post_build_agent(  # noqa: B008
    files: list[UploadFile] = File(..., description="Референсные изображения"),
    request: str = Form("", description="Пожелание/правка пользователя"),
    name_hint: str = Form(""),
) -> dict[str, Any]:
    """Картинки + пожелание → готовый текст агента стиля (окно «+» в помощнике)."""
    uploads = [f for f in files if f and f.filename]
    if not uploads:
        raise HTTPException(status_code=400, detail="Не приложено ни одного изображения")
    if len(uploads) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"Слишком много изображений: {len(uploads)}")
    tmp_dir = Path(tempfile.mkdtemp(prefix="style-agent-"))
    try:
        paths: list[str] = []
        for i, up in enumerate(uploads):
            suffix = Path(up.filename or "").suffix.lower()
            if suffix not in IMG_EXTS:
                raise HTTPException(status_code=400, detail=f"Не изображение: {up.filename}")
            dst = tmp_dir / f"ref_{i:02d}{suffix}"
            with dst.open("wb") as fh:
                shutil.copyfileobj(up.file, fh)
            paths.append(str(dst))
        return await build_style_agent(
            paths, user_request=request, name_hint=name_hint.strip() or None
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


class CategorizeStylesBody(BaseModel):
    entries: list[dict[str, Any]] = Field(..., description="Записи стилей из analyze-style")


@router.post("/categorize-styles")
async def post_categorize_styles(body: CategorizeStylesBody) -> dict[str, Any]:
    """Список стилей → категории по общим критериям."""
    try:
        return await categorize_styles(body.entries)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


_CUSTOM_STYLES_FILE = "gen_assistant_styles.json"


def _custom_styles_path() -> Path:
    return settings.data_dir / _CUSTOM_STYLES_FILE


def _read_custom_styles() -> list[dict[str, Any]]:
    path = _custom_styles_path()
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("gen-assistant custom-styles read failed: {}", e)
        return []
    styles = raw.get("styles") if isinstance(raw, dict) else raw
    return styles if isinstance(styles, list) else []


@router.get("/custom-styles")
async def get_custom_styles() -> dict[str, Any]:
    """Свои стили помощника: диск, не только localStorage браузера."""
    styles = _read_custom_styles()
    return {"styles": styles, "count": len(styles)}


class CustomStylesBody(BaseModel):
    styles: list[dict[str, Any]] = Field(default_factory=list)


@router.put("/custom-styles")
async def put_custom_styles(body: CustomStylesBody) -> dict[str, Any]:
    """Сохранить свои стили на диск (не затираем пустым списком, если на диске уже есть)."""
    incoming = [s for s in body.styles if isinstance(s, dict) and s.get("id") and s.get("promptCore")]
    existing = _read_custom_styles()
    if not incoming and existing:
        return {"styles": existing, "count": len(existing), "kept": True}
    by_id: dict[str, dict[str, Any]] = {}
    for s in existing + incoming:
        sid = str(s.get("id") or "")
        if not sid:
            continue
        prev = by_id.get(sid)
        if prev is None or len(str(s.get("promptCore") or "")) >= len(str(prev.get("promptCore") or "")):
            by_id[sid] = s
    merged = list(by_id.values())
    path = _custom_styles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"styles": merged}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("gen-assistant custom-styles saved n={}", len(merged))
    return {"styles": merged, "count": len(merged)}
