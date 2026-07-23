"""Локальное хранение результатов Create/Grsai на диске.

Структура:
  data/generations/{image|video|audio}/{model}/{YYYYMMDD}/{HHMMSS}_{id}.{ext}
  + sidecar .json с промптом и параметрами
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.settings import settings

_SAFE = re.compile(r"[^a-zA-Z0-9._-]+")


def generations_root() -> Path:
    root = settings.data_dir / "generations"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_segment(value: str, fallback: str = "unknown") -> str:
    s = _SAFE.sub("-", (value or "").strip())[:80].strip("-._")
    return s or fallback


def build_generation_path(
    *,
    media: str,
    model: str,
    ext: str,
) -> Path:
    """Путь для нового файла результата (папки создаются)."""
    media = _safe_segment(media, "image")
    model = _safe_segment(model, "model")
    now = datetime.now(timezone.utc).astimezone()
    day = now.strftime("%Y%m%d")
    stamp = now.strftime("%H%M%S")
    short = uuid.uuid4().hex[:10]
    folder = generations_root() / media / model / day
    folder.mkdir(parents=True, exist_ok=True)
    suf = ext if ext.startswith(".") else f".{ext}"
    return folder / f"{stamp}_{short}{suf}"


def write_sidecar(
    media_path: Path,
    *,
    media: str,
    model: str,
    prompt: str,
    params: dict[str, Any] | None = None,
    raw_url: str | None = None,
    quote: dict[str, Any] | None = None,
    provider: str = "grsai",
) -> Path:
    """JSON рядом с файлом — чтобы на диске был полный контекст генерации."""
    meta = {
        "id": media_path.stem,
        "media": media,
        "model": model,
        "prompt": prompt,
        "params": params or {},
        "provider": provider,
        "raw_url": raw_url,
        "quote": quote,
        "file": media_path.name,
        "path": str(media_path.resolve()),
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "bytes": media_path.stat().st_size if media_path.is_file() else 0,
    }
    side = media_path.with_suffix(".json")
    side.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return side


def list_generation_files(
    *,
    kind: str = "all",
    limit: int = 80,
) -> list[dict[str, Any]]:
    """Скан data/generations (+ legacy grsai_history) для Create history."""
    items: list[dict[str, Any]] = []
    root = generations_root()
    media_filter = None if kind == "all" else kind
    exts = {
        "image": {".png", ".jpg", ".jpeg", ".webp"},
        "video": {".mp4", ".webm"},
        "audio": {".mp3", ".wav", ".m4a", ".ogg"},
    }

    def _add(fp: Path, media: str) -> None:
        if media_filter and media != media_filter:
            return
        if not fp.is_file():
            return
        meta_path = fp.with_suffix(".json")
        label = fp.stem[:16]
        prompt = None
        model = fp.parent.parent.name if fp.parent.parent != root else None
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                prompt = meta.get("prompt")
                label = (meta.get("model") or label)[:24]
                model = meta.get("model") or model
            except Exception:  # noqa: BLE001
                pass
        items.append(
            {
                "id": f"gen-{fp.name}",
                "kind": media,
                "artifact_kind": "generation",
                "preview_url": f"/api/files?path={fp.resolve()}",
                "path": str(fp.resolve()),
                "label": label,
                "project_id": None,
                "project_slug": "local",
                "frame_id": None,
                "prompt": prompt,
                "model": model,
                "mtime": fp.stat().st_mtime,
            }
        )

    if root.is_dir():
        for media_dir in root.iterdir():
            if not media_dir.is_dir():
                continue
            media = media_dir.name
            if media not in exts:
                continue
            allow = exts[media]
            for fp in media_dir.rglob("*"):
                if fp.is_file() and fp.suffix.lower() in allow:
                    _add(fp, media)

    # legacy flat grsai_history
    legacy = settings.data_dir / "grsai_history"
    if legacy.is_dir():
        for fp in legacy.iterdir():
            if not fp.is_file():
                continue
            suf = fp.suffix.lower()
            if suf in exts["image"]:
                _add(fp, "image")
            elif suf in exts["video"]:
                _add(fp, "video")
            elif suf in exts["audio"]:
                _add(fp, "audio")

    items.sort(key=lambda x: x.get("mtime") or 0, reverse=True)
    for it in items:
        it.pop("mtime", None)
    return items[:limit]
