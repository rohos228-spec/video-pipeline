"""Хранилище: размеры проектов, экспорт архива, очистка временных кэшей."""
from __future__ import annotations

import asyncio
import os
import shutil
import time
import zipfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.settings import settings
from app.models import Project
from app.web.deps import get_session

router = APIRouter(tags=["storage"])

EXT = {
    "video": {".mp4", ".mov", ".webm", ".mkv", ".avi", ".m4v"},
    "image": {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"},
    "audio": {".mp3", ".wav", ".ogg", ".m4a", ".flac", ".aac"},
    "prompts": {".md", ".txt", ".json", ".xlsx", ".csv", ".yaml", ".yml"},
}
TYPES = ["video", "image", "audio", "prompts", "other"]
_CACHE: dict = {"t": 0.0, "data": None}
TTL = 60.0
EXPORT_DIR = Path.home() / "Downloads" / "via-exports"


def _kind(name: str) -> str:
    e = os.path.splitext(name)[1].lower()
    for k, s in EXT.items():
        if e in s:
            return k
    return "other"


def _scan(root: Path) -> tuple[dict, int]:
    by = {k: 0 for k in TYPES}
    files = 0
    if root.is_dir():
        for dp, _dn, fn in os.walk(root):
            for f in fn:
                try:
                    sz = os.path.getsize(os.path.join(dp, f))
                except OSError:
                    continue
                by[_kind(f)] += sz
                files += 1
    return by, files


def _dir_size(p: Path) -> int:
    return sum(_scan(p)[0].values())


def _cache_dirs() -> list[Path]:
    """Только явно временные папки в data_dir (не результаты проектов)."""
    base = Path(settings.data_dir)
    # .cache целиком НЕ трогаем: там модели (nemo, torch), конфиги и скрипты.
    out = [base / ".cache" / "temp", base / "tmp_gpt"]
    out += [p for p in base.glob("_tmp_*") if p.is_dir()]
    return [p for p in out if p.is_dir()]


def _usage(projects: list[tuple[int, str, str, Path]]) -> dict:
    tot = {k: 0 for k in TYPES}
    rows = []
    files_all = 0
    for pid, slug, title, d in projects:
        by, files = _scan(d)
        for k in TYPES:
            tot[k] += by[k]
        files_all += files
        rows.append({"id": pid, "slug": slug, "title": title, "path": str(d), "bytes": sum(by.values()), "files": files, "by_type": by})
    du = shutil.disk_usage(Path(settings.data_dir).resolve().anchor or settings.data_dir)
    return {
        "generated_at": time.time(),
        "total_bytes": sum(tot.values()),
        "files": files_all,
        "by_type": tot,
        "projects": rows,
        "disk": {"free": du.free, "total": du.total, "used": du.used, "drive": Path(settings.data_dir).resolve().anchor},
        "limit_bytes": du.free,
    }


async def _projects(session: AsyncSession) -> list[tuple[int, str, str, Path]]:
    res = await session.execute(select(Project))
    out = []
    for p in res.scalars().all():
        try:
            out.append((p.id, p.slug, p.title or p.slug, Path(p.data_dir)))
        except Exception:  # noqa: BLE001
            continue
    return out


@router.get("/storage/usage")
async def storage_usage(refresh: bool = False, session: AsyncSession = Depends(get_session)):
    now = time.time()
    if not refresh and _CACHE["data"] is not None and now - _CACHE["t"] < TTL:
        return {**_CACHE["data"], "cached": True}
    data = await asyncio.to_thread(_usage, await _projects(session))
    _CACHE.update(t=time.time(), data=data)
    return {**data, "cached": False}


@router.get("/storage/cache")
async def storage_cache_info():
    dirs = await asyncio.to_thread(lambda: [{"path": str(p), "bytes": _dir_size(p)} for p in _cache_dirs()])
    return {"dirs": dirs, "total_bytes": sum(d["bytes"] for d in dirs)}


@router.post("/storage/cache/clear")
async def storage_cache_clear():
    def run():
        freed = 0
        for p in _cache_dirs():
            for child in p.iterdir():
                try:
                    sz = _dir_size(child) if child.is_dir() else child.stat().st_size
                    shutil.rmtree(child) if child.is_dir() else child.unlink()
                    freed += sz
                except OSError:
                    pass
        return freed
    freed = await asyncio.to_thread(run)
    _CACHE["data"] = None
    return {"freed_bytes": freed}


@router.post("/storage/export/{project_id}")
async def storage_export(project_id: int, session: AsyncSession = Depends(get_session)):
    p = await session.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    src = Path(p.data_dir)
    if not src.is_dir():
        raise HTTPException(404, "project dir missing")
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{p.slug}-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    dst = EXPORT_DIR / name

    def run():
        with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
            for dp, _dn, fn in os.walk(src):
                for f in fn:
                    fp = Path(dp) / f
                    z.write(fp, fp.relative_to(src.parent))
        return dst.stat().st_size

    size = await asyncio.to_thread(run)
    return {"path": str(dst), "name": name, "bytes": size, "url": f"/api/storage/exports/{name}"}


@router.get("/storage/exports/{name}")
async def storage_export_download(name: str):
    fp = (EXPORT_DIR / name).resolve()
    if fp.parent != EXPORT_DIR.resolve() or not fp.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(fp, filename=name, media_type="application/zip")
