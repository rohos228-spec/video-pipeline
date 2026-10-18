"""Local ratings (1-5 stars) and node reviews, stored as JSON under data/."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException, Query

from app.settings import settings

router = APIRouter(tags=["ratings"])
_lock = threading.Lock()
DEFAULT_RATING = 5


def _file() -> Path:
    p = Path(settings.data_dir) / "ratings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _load() -> dict:
    try:
        d = json.loads(_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _save(d: dict) -> None:
    f = _file()
    tmp = f.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(f)


@router.get("/ratings")
async def get_ratings(kind: str = Query(..., pattern="^(node|prompt)$")) -> dict:
    """{id: rating} for everything voted on; missing ids are 5 by default."""
    votes = _load().get("votes", {}).get(kind, {})
    return {"default": DEFAULT_RATING, "ratings": {k: v for k, v in votes.items() if isinstance(v, int)}}


@router.post("/ratings")
async def post_rating(payload: dict = Body(...)) -> dict:
    kind = str(payload.get("kind") or "")
    item = str(payload.get("id") or "").strip()
    try:
        value = int(payload.get("value"))
    except (TypeError, ValueError):
        value = 0
    if kind not in ("node", "prompt") or not item or not 1 <= value <= 5:
        raise HTTPException(status_code=400, detail="kind/id/value(1-5) required")
    with _lock:
        d = _load()
        d.setdefault("votes", {}).setdefault(kind, {})[item] = value
        _save(d)
    return {"id": item, "rating": value}


_PLACEHOLDER = [
    {"author": "VIA Studio", "rating": 5, "text": "Стабильно работает в пайплайне, настройки по умолчанию подходят для большинства роликов.", "placeholder": True},
    {"author": "Монтажёр", "rating": 5, "text": "Удобно: результат сразу виден в меню ноды, промт можно поменять из каталога.", "placeholder": True},
]


@router.get("/node-reviews/{node_type}")
async def get_reviews(node_type: str) -> dict:
    own = _load().get("reviews", {}).get(node_type, [])
    return {"reviews": list(reversed(own)) + _PLACEHOLDER}


@router.post("/node-reviews/{node_type}")
async def post_review(node_type: str, payload: dict = Body(...)) -> dict:
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="пустой отзыв")
    author = str(payload.get("author") or "").strip()[:60] or "Я"
    try:
        rating = max(1, min(5, int(payload.get("rating") or 5)))
    except (TypeError, ValueError):
        rating = 5
    rev = {"author": author, "rating": rating, "text": text[:2000], "ts": time.time()}
    with _lock:
        d = _load()
        d.setdefault("reviews", {}).setdefault(node_type, []).append(rev)
        _save(d)
    return rev
