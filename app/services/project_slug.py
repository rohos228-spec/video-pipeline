"""Свободен ли slug на диске.

Удаление проекта убирает строку из базы, а папка `data/videos/<slug>` с его project.db и медиа остаётся. Новый проект
с тем же slug подхватывал чужую project.db и отвечал 500 (UNIQUE projects.id), а старые кадры и ролики подмешивались.
"""
from __future__ import annotations

from pathlib import Path

from app.settings import settings


def slug_taken_on_disk(slug: str) -> bool:
    folder = Path(settings.data_dir) / "videos" / slug
    try:
        return folder.is_dir() and any(folder.iterdir())
    except OSError:
        return True
