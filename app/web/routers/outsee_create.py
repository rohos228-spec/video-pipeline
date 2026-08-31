"""Глобальный Outsee Create: настройки и история — не привязаны к проекту."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Frame, Project
from app.settings import settings
from app.web.deps import get_session

router = APIRouter(prefix="/outsee-create", tags=["outsee-create"])

_SETTINGS_FILE = "outsee_create_settings.json"
_SETTINGS_LOCK = asyncio.Lock()

_DEFAULT_SETTINGS: dict[str, Any] = {
    "media_type": "image",
    "image_slug": "gpt-image-2",
    "video_slug": "sora-2",
    "audio_slug": "suno-5-5",
    "aspect": "16:9",
    "image_resolution": "1K",
    "image_quality": "medium",
    "image_relax": False,
    "video_resolution": "1080p",
    "video_relax": False,
    "duration": "5",
    "generate_audio": False,
    "orientation": "video",
    "motion_quality": "std",
    "instrumental": False,
    "prompt": "",
    # outsee | kie — для кнопки «Генерировать» в Create
    "image_provider": "outsee",
    "video_provider": "outsee",
    "sora_size": "small",
}


def _settings_path() -> Path:
    path = settings.data_dir / _SETTINGS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_settings() -> dict[str, Any]:
    path = _settings_path()
    if not path.is_file():
        return dict(_DEFAULT_SETTINGS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("outsee_create settings read failed: {}", e)
        return dict(_DEFAULT_SETTINGS)
    if not isinstance(raw, dict):
        return dict(_DEFAULT_SETTINGS)
    out = dict(_DEFAULT_SETTINGS)
    for k, v in raw.items():
        if k in _DEFAULT_SETTINGS:
            out[k] = v
    return out


def _save_settings(data: dict[str, Any]) -> dict[str, Any]:
    out = dict(_DEFAULT_SETTINGS)
    for k in _DEFAULT_SETTINGS:
        if k in data:
            out[k] = data[k]
    path = _settings_path()
    # атомарная запись: параллельные PUT не портят JSON
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return out


@router.get("/settings")
async def get_outsee_create_settings() -> dict[str, Any]:
    return _load_settings()


@router.put("/settings")
async def put_outsee_create_settings(body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be object")
    async with _SETTINGS_LOCK:
        return await asyncio.to_thread(_save_settings, body)


def _preview_for_artifact(a: Artifact) -> str | None:
    if a.uuid:
        return f"/api/artifacts/{a.uuid}/file"
    return None


@router.get("/history")
async def list_outsee_create_history(
    kind: str = Query("all", pattern="^(all|image|video|audio)$"),
    limit: int = Query(80, ge=1, le=500),
    scope: str = Query(
        "create",
        pattern="^(create|all)$",
        description="create = только data/generations; all = + артефакты проектов",
    ),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """История: локальные Create-файлы; артефакты проектов — только scope=all."""
    from app.services.generation_storage import list_generation_files

    create_budget = min(120, limit)
    out: list[dict[str, Any]] = list(
        list_generation_files(kind=kind, limit=create_budget)
    )
    if scope != "all":
        return out[:limit]

    seen_ids = {x["id"] for x in out}

    kind_filter: set[ArtifactKind]
    if kind == "image":
        kind_filter = {
            ArtifactKind.scene_image,
            ArtifactKind.hero_reference,
            ArtifactKind.item_reference,
        }
    elif kind == "video":
        kind_filter = {ArtifactKind.scene_video, ArtifactKind.final_video}
    elif kind == "audio":
        kind_filter = {ArtifactKind.audio}
    else:
        kind_filter = {
            ArtifactKind.scene_image,
            ArtifactKind.hero_reference,
            ArtifactKind.item_reference,
            ArtifactKind.scene_video,
            ArtifactKind.final_video,
            ArtifactKind.audio,
        }

    projects = {
        p.id: p
        for p in (await session.execute(select(Project))).scalars().all()
    }
    remain = max(0, limit - len(out))
    arts = (
        await session.execute(
            select(Artifact)
            .where(Artifact.kind.in_(kind_filter))
            .order_by(Artifact.id.desc())
            .limit(remain if remain else 1)
        )
    ).scalars().all() if remain else []

    frame_ids = {a.frame_id for a in arts if a.frame_id}
    frames: dict[int, Frame] = {}
    if frame_ids:
        frames = {
            f.id: f
            for f in (
                await session.execute(select(Frame).where(Frame.id.in_(frame_ids)))
            ).scalars().all()
        }

    for a in arts:
        aid = a.uuid or f"art-{a.id}"
        if aid in seen_ids:
            continue
        proj = projects.get(a.project_id)
        fr = frames.get(a.frame_id) if a.frame_id else None
        a_kind = a.kind.value if hasattr(a.kind, "value") else str(a.kind)
        media = "image"
        if a.kind in (ArtifactKind.scene_video, ArtifactKind.final_video):
            media = "video"
        elif a.kind == ArtifactKind.audio:
            media = "audio"
        label = a_kind
        if fr is not None:
            label = f"frame_{int(getattr(fr, 'number', 0) or 0):03d}"
        out.append(
            {
                "id": aid,
                "kind": media,
                "artifact_kind": a_kind,
                "preview_url": _preview_for_artifact(a),
                "path": a.path,
                "label": label,
                "project_id": a.project_id,
                "project_slug": getattr(proj, "slug", None) if proj else None,
                "frame_id": a.frame_id,
                "prompt": (
                    getattr(fr, "image_prompt", None)
                    or getattr(fr, "animation_prompt", None)
                    or getattr(fr, "voiceover_text", None)
                    if fr
                    else None
                ),
                "status": "done",
            }
        )
        seen_ids.add(aid)
        if len(out) >= limit:
            break

    # Disk fallback только если Create-локал + DB всё ещё мало
    if len(out) < min(40, limit):
        for pid, p in projects.items():
            base = p.data_dir
            if not base.is_dir():
                continue
            for sub, media in (
                ("scenes", "image"),
                ("images", "image"),
                ("videos", "video"),
                ("clips", "video"),
                ("audio", "audio"),
            ):
                if kind not in ("all", media):
                    continue
                d = base / sub
                if not d.is_dir():
                    continue
                try:
                    files = sorted(
                        d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True
                    )
                except OSError:
                    continue
                for fp in files[:30]:
                    if not fp.is_file():
                        continue
                    if fp.suffix.lower() not in {
                        ".png",
                        ".jpg",
                        ".jpeg",
                        ".webp",
                        ".mp4",
                        ".webm",
                        ".mp3",
                        ".wav",
                        ".m4a",
                    }:
                        continue
                    key = f"disk-{pid}-{fp.name}"
                    if key in seen_ids:
                        continue
                    out.append(
                        {
                            "id": key,
                            "kind": media,
                            "artifact_kind": "file",
                            "preview_url": f"/api/files?path={fp}",
                            "path": str(fp),
                            "label": fp.name,
                            "project_id": pid,
                            "project_slug": p.slug,
                            "frame_id": None,
                            "prompt": None,
                            "status": "done",
                        }
                    )
                    seen_ids.add(key)
                    if len(out) >= limit:
                        return out[:limit]

    return out[:limit]


@router.delete("/history")
async def delete_outsee_create_history_item(
    path: str | None = Query(None),
    item_id: str | None = Query(None),
) -> dict[str, Any]:
    """Удалить файл генерации из локальной истории Create."""
    from app.services.generation_storage import delete_generation_item

    ok = delete_generation_item(path=path, item_id=item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Файл генерации не найден")
    return {"ok": True, "deleted": True}


@router.get("/download")
async def download_media(
    path: str | None = Query(None),
    url: str | None = Query(None),
    format: str = Query("png"),
    filename: str | None = Query(None),
):
    """Скачивание медиафайла с конвертацией формата (PNG, JPG, WEBP) и корректным Content-Disposition."""
    import io
    import re
    from fastapi.responses import FileResponse, Response
    import httpx
    from PIL import Image

    target_format = (format or "png").lower().strip()
    if target_format not in {"png", "jpg", "jpeg", "webp", "mp4", "mp3", "wav"}:
        target_format = "png"

    clean_base = re.sub(r"[^\w\-_.]", "_", (filename or "generation").strip())
    clean_base = re.sub(r"\.(png|jpg|jpeg|webp|mp4|mp3|wav)$", "", clean_base, flags=re.IGNORECASE)
    out_filename = f"{clean_base}.{target_format}"

    local_path: Path | None = None
    if path:
        p = Path(path)
        if p.is_file():
            local_path = p
    elif url and url.startswith("/api/generations/"):
        rel = url.replace("/api/generations/", "")
        p = Path("data/generations") / rel
        if p.is_file():
            local_path = p

    if local_path and local_path.suffix.lower() in {".mp4", ".webm", ".mov", ".mp3", ".wav"}:
        return FileResponse(
            str(local_path),
            filename=out_filename,
            headers={"Content-Disposition": f'attachment; filename="{out_filename}"'},
        )

    img_bytes: bytes | None = None
    if local_path and local_path.is_file():
        img_bytes = local_path.read_bytes()
    elif url:
        try:
            target_url = url
            if target_url.startswith("/"):
                target_url = f"http://127.0.0.1:8765{target_url}"
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(target_url)
                if resp.status_code == 200:
                    img_bytes = resp.content
        except Exception as e:
            logger.warning("download proxy fetch error: {}", e)

    if not img_bytes:
        raise HTTPException(status_code=404, detail="Файл не найден для скачивания")

    try:
        pil_img = Image.open(io.BytesIO(img_bytes))
        out_buf = io.BytesIO()

        if target_format in {"jpg", "jpeg"}:
            if pil_img.mode in ("RGBA", "LA", "P"):
                bg = Image.new("RGB", pil_img.size, (255, 255, 255))
                if pil_img.mode == "RGBA":
                    bg.paste(pil_img, mask=pil_img.split()[3])
                else:
                    bg.paste(pil_img.convert("RGBA"))
                pil_img = bg
            elif pil_img.mode != "RGB":
                pil_img = pil_img.convert("RGB")
            pil_img.save(out_buf, format="JPEG", quality=95)
            media_type = "image/jpeg"
        elif target_format == "webp":
            pil_img.save(out_buf, format="WEBP", quality=95)
            media_type = "image/webp"
        else:
            pil_img.save(out_buf, format="PNG")
            media_type = "image/png"

        return Response(
            content=out_buf.getvalue(),
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{out_filename}"'},
        )
    except Exception as e:
        logger.warning("download PIL convert fallback: {}", e)
        return Response(
            content=img_bytes,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{out_filename}"'},
        )


from pydantic import BaseModel


class EnhancePromptRequest(BaseModel):
    prompt: str
    style: str | None = None


def _smart_enhance_prompt(text: str, style: str | None = None) -> str:
    """Интеллектуальная переработка промпта в художественный английский стиль."""
    dict_map = {
        "девушка": "a stunning beautiful young woman with windblown hair",
        "девушка в горах": "a young female traveler standing on a rocky mountain path in the Scottish Highlands, looking over misty sunlit valleys",
        "парень": "a handsome charismatic young man",
        "мужчина": "a rugged charismatic man with intense gaze",
        "женщина": "an elegant graceful woman in stylish attire",
        "рыцарь": "a valiant noble knight in intricate ornate plate armor standing ready",
        "горы": "majestic snow-capped mountain peaks in the Scottish Highlands with misty valleys",
        "лес": "an enchanted mystical ancient forest with towering mossy trees and sunbeams",
        "замок": "a monumental gothic stone fortress perched high upon dramatic sea cliffs",
        "космос": "an epic celestial nebula deep in outer space with sparkling spiral galaxies",
        "город": "a sprawling futuristic cyberpunk metropolis with glowing neon lights and flying traffic",
        "киберпанк": "a futuristic cyberpunk setting with holographic billboards and wet asphalt reflections",
        "кот": "a majestic fluffy cat with striking intelligent eyes",
        "кошка": "a graceful sleek cat with glowing amber eyes",
        "собака": "a noble loyal dog with expressive eyes in scenic nature",
        "машина": "a sleek high-end futuristic concept sports car on a coastal highway",
        "автомобиль": "a futuristic supercar with aerodynamic curves and glowing headlights",
        "море": "a dramatic storm over a vast crystal clear ocean with crashing waves",
        "океан": "an expansive deep turquoise ocean under dramatic skies",
        "закат": "a breathtaking golden hour sunset casting warm radiant glow across scenic landscape",
        "рассвет": "a soft ethereal morning dawn with golden rays filtering through mist",
        "воин": "a fierce battle-hardened warrior wielding ancient weapons",
        "маг": "a powerful sorcerer channeling glowing arcane energy from hands",
        "дракон": "a colossal majestic ancient dragon with glistening iridescent scales",
        "робот": "an advanced sleek humanoid android with subtle glowing optic circuits",
    }
    
    lower = text.lower().strip()
    scene_subject = text
    for ru, en in dict_map.items():
        if ru in lower:
            scene_subject = en
            break

    style_details = {
        "photo": "shot on 35mm lens, f/1.8 aperture, natural skin pores, authentic realistic textures, award-winning photography, soft natural illumination",
        "cinema": "cinematic movie still, anamorphic lens flare, Arri Alexa 65, Panavision framing, dramatic color grading, atmospheric haze",
        "3d": "Octane Render 3D masterpiece, Pixar style CGI, raytracing reflections, subsurface scattering, 8k resolution, impeccable modeling",
        "anime": "Makoto Shinkai aesthetic anime artwork, vibrant colors, detailed sky with fluffy clouds, painterly background, Studio Ghibli vibes",
        "art": "classical oil on canvas painting, expressive impasto brushstrokes, dramatic chiaroscuro Rembrandt lighting, rich color palette",
        "cyberpunk": "cyberpunk 2077 aesthetic, volumetric neon pink and cyan lighting, wet rain-soaked streets, gritty high-tech details, futuristic atmosphere",
        "fantasy": "high fantasy digital illustration, glowing magical particles, whimsical atmosphere, ArtStation trending epic composition, intricate details",
    }

    style_part = style_details.get(
        style or "",
        "cinematic lighting, shot on 35mm lens, sharp focus, natural realistic textures, volumetric depth of field, 8k resolution masterwork, detailed atmospheric background"
    )

    if any(ch in "abcdefghijklmnopqrstuvwxyz" for ch in lower) and not any(ch in "абвгдеёжзийклмнопрстуфхцчшщъыьэюя" for ch in lower):
        return f"A breathtaking scene of {text}, {style_part}"
    
    return f"A breathtaking cinematic visual of {scene_subject}, {style_part}"


@router.post("/enhance-prompt")
async def enhance_prompt_endpoint(req: EnhancePromptRequest) -> dict[str, Any]:
    """Улучшить промпт с помощью LLM (GPT / Kimi / Vibecode / Kie.ai) или интеллектуального расширителя."""
    import httpx

    text = (req.prompt or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Промпт не может быть пустым")

    system_prompt = (
        "You are an expert Midjourney and visual AI prompt engineer. "
        "Your job is to rewrite, expand, and enhance the user's image prompt into a rich, "
        "highly detailed, visually stunning photorealistic prompt in English. "
        "Describe lighting (e.g. volumetric rays, golden hour, cinematic lighting), camera angle, "
        "mood, atmosphere, fine textures, depth of field, and compositional details. "
        "Keep the final prompt concise (between 30 to 75 words). "
        "Output ONLY the final enhanced prompt text without any introductory text, quotes, or markdown codeblocks."
    )

    # 1. Попытка через основной текстовый LLM (OpenAI / TokenRouter Kimi / Vibecode)
    try:
        from app.services.gpt_client import ApiGptClient, gpt_text_via_api

        if gpt_text_via_api():
            gpt = ApiGptClient()
            full_prompt = (
                f"{system_prompt}\n\n"
                f"Enhance this image prompt for visual AI generation:\n\n{text}\n\n"
                f"Enhanced prompt (in English):"
            )
            enhanced = await gpt.ask_fresh(full_prompt, timeout=30)
            cleaned = enhanced.strip().strip('"').strip("'").strip("`")
            if cleaned and len(cleaned) > 10:
                logger.info("AI prompt enhanced via ApiGptClient: {} -> {}", text[:30], cleaned[:60])
                return {"ok": True, "enhanced_prompt": cleaned, "provider": "llm"}
    except Exception as e:
        logger.warning("LLM prompt enhance (ApiGptClient) fallback: {}", e)

    # 2. Попытка через KIE_API_KEY (kie.ai chat completions)
    try:
        from app.bots import kie_http
        from app.bots.kie_kling import kie_api_base_url, kie_api_key

        if kie_http.kie_configured():
            key = kie_api_key()
            base = kie_api_base_url()
            url = f"{base}/api/v1/chat/completions"
            payload = {
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Enhance this image prompt for visual AI generation:\n\n{text}"},
                ],
                "temperature": 0.7,
                "max_tokens": 300,
            }
            async with httpx.AsyncClient(timeout=20.0) as client:
                r = await client.post(url, headers={"Authorization": f"Bearer {key}"}, json=payload)
                if r.status_code == 200:
                    data = r.json()
                    choices = data.get("choices") or []
                    if choices and isinstance(choices, list):
                        msg = choices[0].get("message", {}).get("content", "")
                        cleaned = msg.strip().strip('"').strip("'").strip("`")
                        if cleaned and len(cleaned) > 10:
                            logger.info("AI prompt enhanced via Kie.ai chat: {} -> {}", text[:30], cleaned[:60])
                            return {"ok": True, "enhanced_prompt": cleaned, "provider": "kie-llm"}
    except Exception as e:
        logger.warning("Kie LLM prompt enhance fallback: {}", e)

    # 3. Умный локальный генератор (если ключи LLM не заданы)
    fallback_prompt = _smart_enhance_prompt(text, req.style)
    return {"ok": True, "enhanced_prompt": fallback_prompt, "provider": "synthesizer"}

