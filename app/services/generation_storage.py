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
    status: str = "done",
    job_id: str | None = None,
    error: str | None = None,
    require_file: bool = True,
) -> Path:
    """JSON рядом с файлом — чтобы на диске был полный контекст генерации.

    `require_file=False` — для pending (файл ещё не скачан).
    """
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
        "status": status,
        "job_id": job_id,
        "error": error,
    }
    if require_file and not media_path.is_file() and status == "done":
        # всё равно пишем sidecar — статус покажет проблему
        meta["status"] = "failed"
        meta["error"] = meta.get("error") or "media file missing"
    side = media_path.with_suffix(".json")
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return side


def update_sidecar(media_path: Path, **updates: Any) -> Path | None:
    """Частично обновить sidecar (status / error / raw_url…)."""
    side = media_path.with_suffix(".json")
    meta: dict[str, Any] = {}
    if side.is_file():
        try:
            meta = json.loads(side.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            meta = {}
    meta.update({k: v for k, v in updates.items() if v is not None or k == "error"})
    meta["path"] = str(media_path.resolve())
    meta["file"] = media_path.name
    if media_path.is_file():
        meta["bytes"] = media_path.stat().st_size
    meta["updated_at"] = (
        datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    )
    side.parent.mkdir(parents=True, exist_ok=True)
    side.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return side


def import_legacy_create_media() -> int:
    """Копирует loose-файлы из outsee_create/grsai_history → data/generations/.

    Старые Create-ответы писали только в flat-папки — история их не видела.
    Исходник не удаляем; повторный импорт блокируется маркером.
    """
    import shutil

    exts = {
        ".png": "image",
        ".jpg": "image",
        ".jpeg": "image",
        ".webp": "image",
        ".mp4": "video",
        ".webm": "video",
        ".mp3": "audio",
        ".wav": "audio",
        ".m4a": "audio",
        ".ogg": "audio",
    }
    marker = generations_root() / ".imported_legacy.txt"
    try:
        imported = (
            set(marker.read_text(encoding="utf-8").splitlines())
            if marker.is_file()
            else set()
        )
    except OSError:
        imported = set()

    moved = 0
    changed = False
    for legacy_name in ("outsee_create", "grsai_history"):
        legacy = settings.data_dir / legacy_name
        if not legacy.is_dir():
            continue
        for fp in legacy.iterdir():
            if not fp.is_file():
                continue
            media = exts.get(fp.suffix.lower())
            if not media:
                continue
            key = f"{legacy_name}/{fp.name}"
            if key in imported:
                continue
            dest = build_generation_path(
                media=media,
                model=legacy_name.replace("_", "-"),
                ext=fp.suffix.lower(),
            )
            try:
                shutil.copy2(fp, dest)
            except OSError:
                continue
            write_sidecar(
                dest,
                media=media,
                model=legacy_name.replace("_", "-"),
                prompt="",
                params={"imported_from": key},
                provider="legacy",
                status="done",
                require_file=True,
            )
            imported.add(key)
            changed = True
            moved += 1
    if changed:
        try:
            marker.write_text("\n".join(sorted(imported)) + "\n", encoding="utf-8")
        except OSError:
            pass
    return moved


def list_generation_files(
    *,
    kind: str = "all",
    limit: int = 80,
    import_legacy: bool = True,
) -> list[dict[str, Any]]:
    """Скан data/generations (+ legacy) — включая pending без файла."""
    if import_legacy:
        try:
            import_legacy_create_media()
        except Exception:  # noqa: BLE001
            pass
    items: list[dict[str, Any]] = []
    root = generations_root()
    media_filter = None if kind == "all" else kind
    exts = {
        "image": {".png", ".jpg", ".jpeg", ".webp"},
        "video": {".mp4", ".webm"},
        "audio": {".mp3", ".wav", ".m4a", ".ogg"},
    }
    seen: set[str] = set()

    def _status_label(status: str, model: str | None) -> str:
        if status == "queued":
            return "в очереди"
        if status == "processing":
            return "генерация…"
        if status == "failed":
            return "ошибка"
        return (model or "file")[:24]

    def _add_from_meta(meta: dict[str, Any], *, mtime: float) -> None:
        media = str(meta.get("media") or "image")
        if media_filter and media != media_filter:
            return
        fp = Path(str(meta.get("path") or ""))
        if not fp.name:
            return
        key = f"gen-{fp.name}"
        if key in seen:
            return
        seen.add(key)
        status = str(meta.get("status") or ("done" if fp.is_file() else "queued"))
        has_file = fp.is_file() and fp.stat().st_size > 32
        if status == "done" and not has_file:
            status = "failed"
        # Старые pending без файла после рестарта бэкенда (не трогаем свежие).
        if status in {"queued", "processing"} and not has_file:
            age_s = max(0.0, datetime.now(timezone.utc).timestamp() - mtime)
            if age_s > 20 * 60:
                status = "failed"
                if not meta.get("error"):
                    err = "прервано (рестарт / сбой задачи)"
                    meta["error"] = err
                    try:
                        update_sidecar(fp, status="failed", error=err)
                    except Exception:  # noqa: BLE001
                        pass
        items.append(
            {
                "id": key,
                "kind": media,
                "artifact_kind": "generation",
                "preview_url": (
                    f"/api/files?path={fp.resolve()}" if has_file else None
                ),
                "path": str(fp.resolve()) if fp else None,
                "label": _status_label(status, meta.get("model")),
                "project_id": None,
                "project_slug": meta.get("provider") or "local",
                "frame_id": None,
                "prompt": meta.get("prompt"),
                "model": meta.get("model"),
                "status": status,
                "job_id": meta.get("job_id"),
                "error": meta.get("error"),
                "mtime": mtime,
            }
        )

    def _add_media_only(fp: Path, media: str) -> None:
        if media_filter and media != media_filter:
            return
        if not fp.is_file():
            return
        key = f"gen-{fp.name}"
        if key in seen:
            return
        meta_path = fp.with_suffix(".json")
        if meta_path.is_file():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                meta.setdefault("path", str(fp.resolve()))
                meta.setdefault("media", media)
                _add_from_meta(meta, mtime=fp.stat().st_mtime)
                return
            except Exception:  # noqa: BLE001
                pass
        seen.add(key)
        items.append(
            {
                "id": key,
                "kind": media,
                "artifact_kind": "generation",
                "preview_url": f"/api/files?path={fp.resolve()}",
                "path": str(fp.resolve()),
                "label": fp.stem[:16],
                "project_id": None,
                "project_slug": "local",
                "frame_id": None,
                "prompt": None,
                "model": fp.parent.parent.name if fp.parent.parent != root else None,
                "status": "done",
                "mtime": fp.stat().st_mtime,
            }
        )

    if root.is_dir():
        # pending / done sidecars first
        for side in root.rglob("*.json"):
            if not side.is_file():
                continue
            try:
                meta = json.loads(side.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            if not isinstance(meta, dict):
                continue
            try:
                mtime = side.stat().st_mtime
            except OSError:
                mtime = 0.0
            _add_from_meta(meta, mtime=mtime)

        for media_dir in root.iterdir():
            if not media_dir.is_dir():
                continue
            media = media_dir.name
            if media not in exts:
                continue
            allow = exts[media]
            for fp in media_dir.rglob("*"):
                if fp.is_file() and fp.suffix.lower() in allow:
                    _add_media_only(fp, media)

    # legacy flat grsai_history + outsee_create
    for legacy_name in ("grsai_history", "outsee_create"):
        legacy = settings.data_dir / legacy_name
        if not legacy.is_dir():
            continue
        for fp in legacy.iterdir():
            if not fp.is_file():
                continue
            suf = fp.suffix.lower()
            if suf in exts["image"]:
                _add_media_only(fp, "image")
            elif suf in exts["video"]:
                _add_media_only(fp, "video")
            elif suf in exts["audio"]:
                _add_media_only(fp, "audio")

    items.sort(key=lambda x: x.get("mtime") or 0, reverse=True)
    for it in items:
        it.pop("mtime", None)
    return items[:limit]
