"""Корень репозитория (pyproject.toml) — пути не зависят от текущей папки в shell."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def find_project_root() -> Path:
    """Папка с pyproject.toml (video-pipeline), даже если CWD = web/."""
    candidates = [
        Path(__file__).resolve().parent.parent,
        Path.cwd(),
        *Path.cwd().parents,
    ]
    seen: set[Path] = set()
    for base in candidates:
        if base in seen:
            continue
        seen.add(base)
        if (base / "pyproject.toml").is_file():
            return base
    return Path(__file__).resolve().parent.parent


def resolve_project_path(path: Path) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    return find_project_root() / p
