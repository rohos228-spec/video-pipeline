"""Помощник генерации: LLM-агент промптов с обвязкой-валидатором."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from app.project_root import find_project_root
from app.settings import settings
from app.services.gen_assistant import (
    MAX_COUNT,
    MIN_COUNT,
    generate_prompts,
)
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
    logger.info(
        "build-agent start files={} hint={!r} request_chars={}",
        len(uploads),
        (name_hint or "").strip(),
        len(request or ""),
    )
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
    except Exception as e:
        logger.exception("build-agent failed")
        raise HTTPException(
            status_code=502,
            detail=f"GPT недоступен ({e}) — агент не собран",
        ) from e
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
_CUSTOM_AGENTS_FILE = "gen_assistant_agents.json"
_STYLE_ID_RE = re.compile(r"^[A-Za-z0-9_]{3,64}$")


def gen_styles_dir() -> Path:
    path = settings.data_dir / "gen_styles"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_style_cover(name: str) -> Path | None:
    """Файл обложки: сначала data/gen_styles, потом собранный web/out и public."""
    safe = Path(name or "").name
    if not safe or safe != name or ".." in name:
        return None
    roots = (
        settings.data_dir / "gen_styles",
        find_project_root() / "web" / "out" / "gen-styles",
        find_project_root() / "web" / "public" / "gen-styles",
    )
    for root in roots:
        cand = root / safe
        if cand.is_file():
            return cand
    return None


def _safe_cover_source(path: str) -> Path:
    fp = Path(path).expanduser().resolve()
    allowed = (
        (settings.data_dir / "generations").resolve(),
        (settings.data_dir / "gen_styles").resolve(),
        Path(tempfile.gettempdir()).resolve(),
    )
    if not any(fp == root or root in fp.parents for root in allowed):
        raise HTTPException(status_code=400, detail="Нельзя взять обложку с этого пути")
    if not fp.is_file():
        raise HTTPException(status_code=400, detail="Файл обложки не найден")
    if fp.suffix.lower() not in IMG_EXTS:
        raise HTTPException(status_code=400, detail="Обложка должна быть изображением")
    return fp


def save_style_cover(*, style_id: str, src: Path) -> str:
    """Копирует картинку в data/gen_styles + web/out|public и пишет cover в JSON."""
    if not _STYLE_ID_RE.match(style_id):
        raise ValueError("Некорректный id стиля")
    suffix = src.suffix.lower() if src.suffix.lower() in IMG_EXTS else ".png"
    dest = gen_styles_dir() / f"{style_id}{suffix}"
    shutil.copyfile(src, dest)
    for extra in (
        find_project_root() / "web" / "out" / "gen-styles",
        find_project_root() / "web" / "public" / "gen-styles",
    ):
        extra.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(src, extra / dest.name)
        except OSError as e:
            logger.warning("style cover copy to {} failed: {}", extra, e)
    url = f"/gen-styles/{dest.name}"
    _set_style_cover(style_id, url)
    logger.info("style cover saved id={} url={}", style_id, url)
    return url


def _set_style_cover(style_id: str, url: str) -> None:
    styles = _read_custom_styles()
    changed = False
    for s in styles:
        if str(s.get("id") or "") != style_id:
            continue
        s["cover"] = url
        s["artUrl"] = url
        changed = True
        break
    if not changed:
        return
    path = _custom_styles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"styles": styles}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _custom_styles_path() -> Path:
    return settings.data_dir / _CUSTOM_STYLES_FILE


def _seed_styles_path() -> Path:
    return find_project_root() / "templates" / "gen_assistant_styles.json"


def _load_styles_file(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("gen-assistant styles read failed {}: {}", path, e)
        return []
    styles = raw.get("styles") if isinstance(raw, dict) else raw
    return [s for s in styles if isinstance(s, dict)] if isinstance(styles, list) else []


def _portable_cover(url: str) -> str:
    text = (url or "").strip()
    if text.startswith("/gen-styles/"):
        return text
    return ""


def _merge_style(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    merged = {**(old or {}), **new}
    for key in ("artUrl", "cover"):
        portable = _portable_cover(str(new.get(key) or "")) or _portable_cover(
            str((old or {}).get(key) or "")
        )
        if portable:
            merged[key] = portable
    old_core = str((old or {}).get("promptCore") or "")
    new_core = str(new.get("promptCore") or "")
    if len(old_core) > len(new_core):
        merged["promptCore"] = old_core
    return merged


def _read_custom_styles() -> list[dict[str, Any]]:
    """Свои стили: seed из репо + data/*.json. Обложки /gen-styles/ важнее локальных путей."""
    by_id: dict[str, dict[str, Any]] = {}
    for s in _load_styles_file(_seed_styles_path()) + _load_styles_file(_custom_styles_path()):
        sid = str(s.get("id") or "")
        if not sid:
            continue
        by_id[sid] = _merge_style(by_id.get(sid), s)
    return list(by_id.values())


def _agents_path() -> Path:
    return settings.data_dir / _CUSTOM_AGENTS_FILE


def _seed_agents_path() -> Path:
    return find_project_root() / "templates" / "gen_assistant_agents.json"


def _load_agents_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("gen-assistant agents read failed {}: {}", path, e)
        return {}
    data = raw.get("agents") if isinstance(raw, dict) else raw
    if not isinstance(data, dict):
        return {}
    return {
        str(k): str(v)
        for k, v in data.items()
        if str(v or "").strip()
    }


def _read_agent_overrides() -> dict[str, str]:
    """Правки вкладки «Агент»: seed + promptCore стилей + диск."""
    out = dict(_load_agents_file(_seed_agents_path()))
    for s in _read_custom_styles():
        sid = str(s.get("id") or "")
        core = str(s.get("promptCore") or "").strip()
        if sid and core and sid not in out:
            out[sid] = core
    out.update(_load_agents_file(_agents_path()))
    return out


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
    incoming = [
        s
        for s in body.styles
        if isinstance(s, dict)
        and s.get("id")
        and s.get("promptCore")
    ]
    existing = _read_custom_styles()
    if not incoming and existing:
        return {"styles": existing, "count": len(existing), "kept": True}
    by_id: dict[str, dict[str, Any]] = {}
    for s in existing + incoming:
        sid = str(s.get("id") or "")
        if not sid:
            continue
        by_id[sid] = _merge_style(by_id.get(sid), s)
    merged = list(by_id.values())
    path = _custom_styles_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"styles": merged}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("gen-assistant custom-styles saved n={}", len(merged))
    return {"styles": merged, "count": len(merged)}


@router.post("/style-cover")
async def post_style_cover(  # noqa: B008
    style_id: str = Form(..., description="id стиля"),
    file: UploadFile | None = File(None),
    source_path: str = Form(""),
) -> dict[str, Any]:
    """Первая картинка стиля / готовая генерация → обложка /gen-styles/{id}.*"""
    sid = (style_id or "").strip()
    if not _STYLE_ID_RE.match(sid):
        raise HTTPException(status_code=400, detail="Некорректный id стиля")
    tmp_dir: Path | None = None
    try:
        if file and file.filename:
            suffix = Path(file.filename).suffix.lower()
            if suffix not in IMG_EXTS:
                raise HTTPException(status_code=400, detail=f"Не изображение: {file.filename}")
            tmp_dir = Path(tempfile.mkdtemp(prefix="style-cover-"))
            src = tmp_dir / f"cover{suffix}"
            with src.open("wb") as fh:
                shutil.copyfileobj(file.file, fh)
        elif (source_path or "").strip():
            src = _safe_cover_source(source_path.strip())
        else:
            raise HTTPException(status_code=400, detail="Нужен файл или путь к генерации")
        url = save_style_cover(style_id=sid, src=src)
        return {"ok": True, "style_id": sid, "cover": url}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    finally:
        if tmp_dir is not None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@router.get("/agent-overrides")
async def get_agent_overrides() -> dict[str, Any]:
    agents = _read_agent_overrides()
    return {"agents": agents, "count": len(agents)}


class AgentOverridesBody(BaseModel):
    agents: dict[str, str] = Field(default_factory=dict)


@router.put("/agent-overrides")
async def put_agent_overrides(body: AgentOverridesBody) -> dict[str, Any]:
    incoming = {
        str(k): str(v)
        for k, v in (body.agents or {}).items()
        if str(k).strip()
        and str(v or "").strip()
    }
    existing = _read_agent_overrides()
    if not incoming and existing:
        return {"agents": existing, "count": len(existing), "kept": True}
    merged = {**existing, **incoming}
    path = _agents_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"agents": merged}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    logger.info("gen-assistant agent-overrides saved n={}", len(merged))
    return {"agents": merged, "count": len(merged)}
