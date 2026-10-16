from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path


def make_workdir(prefix: str = "stand_") -> Path:
    override = os.environ.get("VP_STAND_WORKDIR")
    base = Path(override) if override else Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "video-pipeline-temp" / "stand-tests"
    base.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=base))


def remove_workdir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
