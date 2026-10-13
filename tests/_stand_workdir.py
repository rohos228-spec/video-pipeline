"""A work folder for the e2e stand tests that lives OUTSIDE the repository.

The shared pytest temp folder may sit inside ``data/`` (the app moves TEMP there), and the e2e guard
(``scripts/e2e_guard.py``) rightly refuses a work folder that overlaps live data."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def make_workdir(prefix: str = "stand_") -> Path:
    override = os.environ.get("VP_STAND_WORKDIR")  # CI / чужая машина: явная папка вне репозитория
    base = Path(override) if override else Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "video-pipeline-temp" / "stand-tests"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=base))


def remove_workdir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
