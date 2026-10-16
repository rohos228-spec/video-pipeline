from __future__ import annotations

from pathlib import Path

from app.settings import settings


def slug_taken_on_disk(slug: str) -> bool:
    folder = Path(settings.data_dir) / "videos" / slug
    try:
        return folder.is_dir() and any(folder.iterdir())
    except OSError:
        return True
