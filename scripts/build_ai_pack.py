#!/usr/bin/env python3
"""Собрать папку ai-pack/ — контекст для внешней ИИ / Cursor.

Копирует актуальные SoT-доки (без секретов и data/).
Запуск из корня репо:
  python scripts/build_ai_pack.py
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACK = ROOT / "ai-pack"

# (источник относительно ROOT, назначение внутри ai-pack/)
COPY_MAP: list[tuple[str, str]] = [
    ("AGENTS.md", "00_core/AGENTS.md"),
    ("docs/AGENT_MAP.md", "00_core/AGENT_MAP.md"),
    (".cursor/rules/video-pipeline-ops.mdc", "00_core/video-pipeline-ops.md"),
    (".cursor/rules/video-pipeline-map.mdc", "00_core/video-pipeline-map.md"),
    (".cursor/rules/scene-design-model.mdc", "00_core/scene-design-model.md"),
    ("docs/PROMPT_CONTRACT.md", "01_data_prompts/PROMPT_CONTRACT.md"),
    ("docs/DB_V2.md", "01_data_prompts/DB_V2.md"),
    ("docs/PROMPTS_BLOCKS.md", "01_data_prompts/PROMPTS_BLOCKS.md"),
    ("docs/NODE_SYSTEM.md", "01_data_prompts/NODE_SYSTEM.md"),
    ("docs/OPERATOR_BIBLE.md", "02_ops/OPERATOR_BIBLE.md"),
    ("docs/MASS_CREATION.md", "02_ops/MASS_CREATION.md"),
    (".env.example", "03_config/env.example"),
    ("pyproject.toml", "03_config/pyproject.toml"),
]


def _git_head() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _strip_frontmatter_mdc(text: str) -> str:
    """Убрать YAML frontmatter у .mdc → обычный markdown."""
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) >= 3:
        return parts[2].lstrip("\n")
    return text


def main() -> int:
    PACK.mkdir(parents=True, exist_ok=True)
    for sub in ("00_core", "01_data_prompts", "02_ops", "03_config"):
        (PACK / sub).mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    missing: list[str] = []

    for src_rel, dst_rel in COPY_MAP:
        src = ROOT / src_rel
        dst = PACK / dst_rel
        if not src.is_file():
            missing.append(src_rel)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        raw = src.read_text(encoding="utf-8")
        if src.suffix == ".mdc":
            raw = _strip_frontmatter_mdc(raw)
        dst.write_text(raw, encoding="utf-8", newline="\n")
        copied.append(dst_rel)

    head = _git_head()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    manifest_lines = [
        "ai-pack build",
        f"git_head: {head}",
        f"built_at: {now}",
        f"repo: {ROOT}",
        "",
        "copied:",
        *[f"  {p}" for p in copied],
    ]
    if missing:
        manifest_lines.extend(["", "missing:", *[f"  {p}" for p in missing]])
    (PACK / "MANIFEST.txt").write_text(
        "\n".join(manifest_lines) + "\n", encoding="utf-8", newline="\n"
    )

    print(f"ai-pack OK -> {PACK}")
    print(f"  git HEAD: {head}")
    print(f"  files: {len(copied)}")
    if missing:
        print(f"  MISSING: {', '.join(missing)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
