"""Общие потоки медиа Outsee: картинки + видео пайплайна (0..4).

Один API → один лимит. meta.img_streams (legacy) и meta.outsee_streams —
синонимы. Семафор общий со Studio Create (outsee ≤4).

0 — не звать провайдера (только диск / ошибка если файлов нет).
1 — строго по одному.
2..4 — параллельно до N (img и video делят тот же пул с Create).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from app.models import Project
from app.settings import settings

META_KEY = "img_streams"
META_KEY_ALIAS = "outsee_streams"
INFLIGHT_ATTR = "img_gen_inflight"
VIDEO_INFLIGHT_ATTR = "video_gen_inflight"
IMG_STREAMS_MIN = 0
IMG_STREAMS_MAX = 4


def clamp_img_streams(raw: Any) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = 1
    return max(IMG_STREAMS_MIN, min(IMG_STREAMS_MAX, n))


def default_img_streams() -> int:
    return clamp_img_streams(getattr(settings, "img_max_streams", 1) or 1)


def get_img_streams(project: Project | None) -> int:
    """Потоки Outsee для img+video: meta → env IMG_MAX_STREAMS → 1."""
    if project is not None:
        meta = project.meta if isinstance(project.meta, dict) else {}
        if META_KEY_ALIAS in meta and meta.get(META_KEY_ALIAS) is not None:
            return clamp_img_streams(meta.get(META_KEY_ALIAS))
        if META_KEY in meta and meta.get(META_KEY) is not None:
            return clamp_img_streams(meta.get(META_KEY))
    return default_img_streams()


# Явный алиас: один API — одни потоки.
get_outsee_streams = get_img_streams


def set_img_streams_meta(project: Project, streams: int) -> int:
    from sqlalchemy.orm.attributes import flag_modified

    n = clamp_img_streams(streams)
    meta = dict(project.meta or {})
    meta[META_KEY] = n
    meta[META_KEY_ALIAS] = n
    project.meta = meta
    flag_modified(project, "meta")
    return n


def image_provider_key() -> str:
    """Ключ семафора: grsai | outsee."""
    from app.bots.grsai import grsai_enabled

    if grsai_enabled():
        return "grsai"
    return "outsee"


@asynccontextmanager
async def acquire_image_slot() -> AsyncIterator[None]:
    """Слот провайдера (общий: Create + img + video)."""
    from app.services.create_jobs import _semaphore

    async with _semaphore(image_provider_key()):
        yield


acquire_outsee_slot = acquire_image_slot
