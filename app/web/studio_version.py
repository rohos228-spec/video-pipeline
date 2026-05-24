"""Read web/STUDIO_VERSION — single source for UI build label."""

from __future__ import annotations

from pathlib import Path


def _version_file() -> Path:
    return Path(__file__).resolve().parents[2] / "web" / "STUDIO_VERSION"


def read_studio_version() -> dict[str, str | int]:
    path = _version_file()
    build = 0
    sha = "dev"
    if path.is_file():
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        if lines:
            try:
                build = int(lines[0].strip())
            except ValueError:
                build = 0
        if len(lines) > 1 and lines[1].strip():
            sha = lines[1].strip()
    label = f"v{build} · {sha[:7]}" if sha and sha != "dev" else f"v{build}"
    return {"build": build, "sha": sha, "label": label}


def read_studio_version_label() -> str:
    return str(read_studio_version()["label"])
