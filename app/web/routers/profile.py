"""Профиль пользователя студии: имя/аватар (data/profile.json), статистика, ключи."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import select

from app.settings import settings
from app.web.auth_sessions import require_web_user

router = APIRouter(prefix="/profile", tags=["profile"])

_DATA = Path(settings.data_dir)
_PROFILE = _DATA / "profile.json"
_AVATAR_DIR = _DATA / "profile"
_REPO = Path(__file__).resolve().parents[3]
_ENV = _REPO / ".env"
_BACKUP_DIR = _DATA / "profile-env-backups"

_DEFAULT = {
    "name": "Admin",
    "handle": "admin",
    "bio": "Контент-завод VIA",
    "plan": "Pro",
    "notifications": {"run_done": True, "run_failed": True, "low_balance": True, "weekly": False},
    "created_at": None,
}

# Ключи, которые разрешено показывать (маской) и менять из профиля.
_KEYS = [
    ("gpt", "GPT (текст)", "GPT_API_KEY"),
    ("vibecode", "Vibecode (текст)", "VIBECODE_API_KEY"),
    ("kie", "Kie", "KIE_API_KEY"),
    ("outsee", "Outsee", "OUTSEE_API_KEY"),
    ("elevenlabs", "ElevenLabs", "ELEVENLABS_API_KEY"),
]


def _load() -> dict[str, Any]:
    data = dict(_DEFAULT)
    try:
        if _PROFILE.is_file():
            data.update(json.loads(_PROFILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    if not data.get("created_at"):
        data["created_at"] = datetime.now().isoformat(timespec="seconds")
    return data


def _save(data: dict[str, Any]) -> None:
    _PROFILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _PROFILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(_PROFILE)


def _avatar_file() -> Path | None:
    if _AVATAR_DIR.is_dir():
        for f in sorted(_AVATAR_DIR.glob("avatar.*")):
            return f
    return None


def _public(data: dict[str, Any]) -> dict[str, Any]:
    av = _avatar_file()
    out = dict(data)
    out["avatar_url"] = f"/api/profile/avatar?v={int(av.stat().st_mtime)}" if av else None
    return out


class ProfileUpdate(BaseModel):
    name: str | None = None
    handle: str | None = None
    bio: str | None = None
    notifications: dict[str, bool] | None = None


@router.get("")
async def get_profile() -> dict[str, Any]:
    data = _load()
    if not _PROFILE.is_file():
        _save(data)  # фиксируем дату создания профиля
    return _public(data)


@router.put("")
async def put_profile(body: ProfileUpdate) -> dict[str, Any]:
    data = _load()
    if body.name is not None:
        n = body.name.strip()[:60]
        if not n:
            raise HTTPException(400, "Имя не может быть пустым")
        data["name"] = n
    if body.handle is not None:
        h = "".join(c for c in body.handle.strip().lstrip("@").lower() if c.isalnum() or c in "_.-")[:32]
        data["handle"] = h or "admin"
    if body.bio is not None:
        data["bio"] = body.bio.strip()[:200]
    if body.notifications is not None:
        cur = dict(data.get("notifications") or {})
        cur.update({k: bool(v) for k, v in body.notifications.items()})
        data["notifications"] = cur
    _save(data)
    return _public(data)


@router.get("/avatar")
async def get_avatar():
    av = _avatar_file()
    if av is None:
        return Response(status_code=204)
    return FileResponse(av, headers={"Cache-Control": "private, max-age=60"})


@router.post("/avatar")
async def post_avatar(file: UploadFile = File(...)) -> dict[str, Any]:
    raw = await file.read()
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(413, "Файл больше 8 МБ")
    _AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    for f in _AVATAR_DIR.glob("avatar.*"):
        f.unlink(missing_ok=True)
    try:
        from io import BytesIO

        from PIL import Image

        im = Image.open(BytesIO(raw)).convert("RGB")
        w, h = im.size
        s = min(w, h)
        im = im.crop(((w - s) // 2, (h - s) // 2, (w - s) // 2 + s, (h - s) // 2 + s)).resize((256, 256))
        im.save(_AVATAR_DIR / "avatar.webp", "WEBP", quality=88)
    except Exception:
        ext = (Path(file.filename or "a.png").suffix or ".png").lower()
        if ext not in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
            raise HTTPException(400, "Нужна картинка")
        (_AVATAR_DIR / f"avatar{ext}").write_bytes(raw)
    return _public(_load())


@router.delete("/avatar")
async def delete_avatar() -> dict[str, Any]:
    if _AVATAR_DIR.is_dir():
        for f in _AVATAR_DIR.glob("avatar.*"):
            f.unlink(missing_ok=True)
    return _public(_load())


# ---------- статистика ----------
_STATS_CACHE: dict[str, Any] = {"t": 0.0, "v": None}


def _scan_project_db(db: Path, since: str) -> tuple[dict[str, int], dict[str, dict[str, int]]]:
    totals: dict[str, int] = {}
    daily: dict[str, dict[str, int]] = {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
    try:
        for kind, n in con.execute("select kind, count(*) from artifacts group by kind"):
            totals[str(kind)] = int(n)
        for day, kind, n in con.execute(
            "select substr(created_at,1,10), kind, count(*) from artifacts where created_at >= ? group by 1,2",
            (since,),
        ):
            daily.setdefault(day, {})[str(kind)] = int(n)
    finally:
        con.close()
    return totals, daily


@router.get("/stats")
async def get_stats() -> dict[str, Any]:
    if _STATS_CACHE["v"] is not None and time.time() - _STATS_CACHE["t"] < 60:
        return _STATS_CACHE["v"]
    from app.db import SessionLocal
    from app.models import Project
    from app.project_db import resolve_project_db_path

    async with SessionLocal() as s:
        projects = (await s.execute(select(Project))).scalars().all()
    since_dt = datetime.now() - timedelta(days=29)
    since = since_dt.strftime("%Y-%m-%d")
    totals: dict[str, int] = {}
    daily: dict[str, dict[str, int]] = {}
    per_project: dict[int, dict[str, int]] = {}
    status_counts: dict[str, int] = {}
    for p in projects:
        st = getattr(p.status, "value", str(p.status))
        status_counts[st] = status_counts.get(st, 0) + 1
        if not p.data_dir:
            continue
        try:
            db = resolve_project_db_path(p.data_dir)
            if not db.is_file():
                continue
            t, d = _scan_project_db(db, since)
        except Exception:
            continue
        per_project[p.id] = t
        for k, v in t.items():
            totals[k] = totals.get(k, 0) + v
        for day, kinds in d.items():
            row = daily.setdefault(day, {})
            for k, v in kinds.items():
                row[k] = row.get(k, 0) + v
    days = []
    for i in range(30):
        day = (since_dt + timedelta(days=i)).strftime("%Y-%m-%d")
        k = daily.get(day, {})
        days.append({
            "date": day,
            "images": k.get("scene_image", 0) + k.get("hero_reference", 0) + k.get("item_reference", 0),
            "videos": k.get("scene_video", 0),
            "audio": k.get("audio", 0) + k.get("music", 0),
            "finals": k.get("final_video", 0),
        })
    gen_total = sum(totals.get(k, 0) for k in ("scene_image", "scene_video", "hero_reference", "item_reference", "audio", "music"))
    v = {
        "projects": len(projects),
        "status_counts": status_counts,
        "completed_videos": totals.get("final_video", 0),
        "published": status_counts.get("published", 0),
        "generations_total": gen_total,
        "totals_by_kind": totals,
        "per_project": per_project,
        "daily": days,
        "spend_is_sample": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _STATS_CACHE.update(t=time.time(), v=v)
    return v


# ---------- ключи ----------
def _mask(v: str) -> str:
    """Маска без кусков ключа: ни начала, ни хвоста."""
    if not v:
        return ""
    return "•" * 8


def _caller_is_studio_admin(token: str | None) -> bool:
    """Legacy-токен студии — админ. Сессия users.db — только role=admin."""
    from app.web.auth_sessions import _TOKENS

    if token and token in _TOKENS:
        return True
    if not token:
        return False
    try:
        from app.web import user_accounts

        user = user_accounts.user_for_session(token)
    except Exception:
        return False
    if user is None:
        return False
    role = user.get("role") if isinstance(user, dict) else user["role"]
    return role == "admin"


def _env_value(env: str) -> str:
    attr = env.lower()
    val = getattr(settings, attr, None)
    if isinstance(val, str) and val:
        return val
    return os.environ.get(env, "")


@router.get("/keys")
async def get_keys(request: Request) -> list[dict[str, Any]]:
    await require_web_user(request)
    out = []
    for kid, label, env in _KEYS:
        val = _env_value(env)
        out.append({"id": kid, "label": label, "env": env, "configured": bool(val), "masked": _mask(val)})
    return out


class KeyUpdate(BaseModel):
    value: str


@router.put("/keys/{key_id}")
async def put_key(key_id: str, body: KeyUpdate, request: Request) -> dict[str, Any]:
    token = await require_web_user(request)
    if settings.web_auth_enabled and not _caller_is_studio_admin(token):
        raise HTTPException(403, "Только администратор студии")
    match = next((k for k in _KEYS if k[0] == key_id), None)
    if match is None:
        raise HTTPException(404, "Неизвестный ключ")
    _, label, env = match
    value = body.value.strip()
    if "\n" in value or "\r" in value or len(value) > 500:
        raise HTTPException(400, "Некорректное значение")
    lines: list[str] = []
    if _ENV.is_file():
        _BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_ENV, _BACKUP_DIR / f"env-{datetime.now():%Y%m%d-%H%M%S}")
        lines = _ENV.read_text(encoding="utf-8").splitlines()
    done = False
    for i, line in enumerate(lines):
        if line.split("=", 1)[0].strip() == env:
            lines[i] = f"{env}={value}"
            done = True
    if not done:
        lines.append(f"{env}={value}")
    _ENV.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[env] = value
    try:
        setattr(settings, env.lower(), value)
    except Exception:
        pass
    return {"id": key_id, "label": label, "env": env, "configured": bool(value), "masked": _mask(value)}
