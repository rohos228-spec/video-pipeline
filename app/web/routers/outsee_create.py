"""Глобальный Outsee Create: настройки и история — не привязаны к проекту."""

from __future__ import annotations

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
    # grsai | outsee — для кнопки «Генерировать» в Create
    "image_provider": "grsai",
    "video_provider": "grsai",
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
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


@router.get("/settings")
async def get_outsee_create_settings() -> dict[str, Any]:
    return _load_settings()


@router.put("/settings")
async def put_outsee_create_settings(body: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be object")
    return _save_settings(body)


def _preview_for_artifact(a: Artifact) -> str | None:
    if a.uuid:
        return f"/api/artifacts/{a.uuid}/file"
    return None


@router.get("/history")
async def list_outsee_create_history(
    kind: str = Query("all", pattern="^(all|image|video|audio)$"),
    limit: int = Query(200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    """История генераций по всем проектам (как аккаунт на outsee)."""
    kind_filter: set[ArtifactKind] | None
    if kind == "image":
        kind_filter = {ArtifactKind.scene_image, ArtifactKind.hero_reference, ArtifactKind.item_reference}
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
    arts = (
        await session.execute(
            select(Artifact)
            .where(Artifact.kind.in_(kind_filter))
            .order_by(Artifact.id.desc())
            .limit(limit)
        )
    ).scalars().all()

    frame_ids = {a.frame_id for a in arts if a.frame_id}
    frames: dict[int, Frame] = {}
    if frame_ids:
        frames = {
            f.id: f
            for f in (
                await session.execute(select(Frame).where(Frame.id.in_(frame_ids)))
            ).scalars().all()
        }

    out: list[dict[str, Any]] = []
    for a in arts:
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
                "id": a.uuid or f"art-{a.id}",
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
            }
        )

    # Локальные результаты Create: data/generations/... (+ legacy grsai_history)
    from app.services.generation_storage import list_generation_files

    for item in list_generation_files(kind=kind, limit=80):
        if any(x["id"] == item["id"] for x in out):
            continue
        out.insert(0, item)

    # Disk fallback: scenes/videos across projects if DB sparse
    if len(out) < 40:
        for pid, p in projects.items():
            base = p.data_dir
            if not base.is_dir():
                continue
            for sub, media in (("scenes", "image"), ("images", "image"), ("videos", "video"), ("clips", "video"), ("audio", "audio")):
                if kind not in ("all", media):
                    continue
                d = base / sub
                if not d.is_dir():
                    continue
                try:
                    files = sorted(d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)
                except OSError:
                    continue
                for fp in files[:30]:
                    if not fp.is_file():
                        continue
                    if fp.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".mp4", ".webm", ".mp3", ".wav", ".m4a"}:
                        continue
                    key = f"disk-{pid}-{fp.name}"
                    if any(x["id"] == key for x in out):
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
                        }
                    )
        # newest first roughly
        out = out[:limit]

    return out[:limit]
