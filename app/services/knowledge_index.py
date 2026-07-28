"""Индекс знаний: prompts/ + docs/ + ключевые модули app/.

Сборка: python scripts/build_knowledge_index.py
Поиск: GET /api/knowledge/search?q=
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from loguru import logger

from app.settings import settings

_REPO_ROOT = Path(__file__).resolve().parents[2]

_DOC_GLOBS = (
    "docs/*.md",
    "prompts/**/*.md",
    "AGENTS.md",
    "HOW_TO_RUN.md",
)

_CODE_FILES = (
    "app/orchestrator/node_registry.py",
    "app/orchestrator/pipeline.py",
    "app/orchestrator/auto_advance.py",
    "app/services/prompt_composer.py",
    "app/services/gpt_workspace.py",
    "app/services/gpt_api.py",
    "app/services/mass_factory.py",
    "app/services/xlsx_step_runners.py",
    "app/services/xlsx_v8_import.py",
    "app/storage/plan_sheet_v8.py",
    "app/bots/outsee_http.py",
    "app/web/routers/outsee_create.py",
    "app/web/routers/gpt_workspace.py",
)

_TOKEN_RE = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9_./-]{2,}")


def index_path() -> Path:
    path = settings.data_dir / "knowledge_index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(_REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _title_from_text(text: str, fallback: str) -> str:
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip()[:120] or fallback
        if s:
            return s[:120]
    return fallback


def _excerpt(text: str, limit: int = 280) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1] + "…"


def _tags_for(rel: str, text: str) -> list[str]:
    tags: list[str] = []
    low = (rel + " " + text[:2000]).lower()
    checks = {
        "prompt": ("prompt", "промт", "blocks", "step-presets"),
        "orchestrator": ("orchestrator", "pipeline", "step_code", "anim_pr"),
        "gpt": ("gpt_workspace", "gpt_api", "gpt_client"),
        "xlsx": ("xlsx", "plan_sheet", "row_", "r48"),
        "mass": ("mass_factory", "mass_creation", "/mass", "batch"),
        "outsee": ("outsee", "create", "grsai"),
        "asr": ("whisper", "asr", "assemble", "montage", "parakeet"),
        "agent": ("agent_map", "agents.md", "operator_bible"),
    }
    for tag, keys in checks.items():
        if any(k in low for k in keys):
            tags.append(tag)
    if rel.startswith("docs/"):
        tags.append("docs")
    if rel.startswith("prompts/"):
        tags.append("prompts")
    if rel.startswith("app/"):
        tags.append("code")
    return sorted(set(tags))


def _iter_source_files() -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for pattern in _DOC_GLOBS:
        for p in _REPO_ROOT.glob(pattern):
            if not p.is_file():
                continue
            key = _rel(p)
            if key in seen:
                continue
            # skip huge archaeology dumps in prompts if needed — keep all md for now
            seen.add(key)
            found.append(p)
    for rel in _CODE_FILES:
        p = _REPO_ROOT / rel
        if p.is_file():
            key = _rel(p)
            if key not in seen:
                seen.add(key)
                found.append(p)
    # orchestrator steps
    steps = _REPO_ROOT / "app" / "orchestrator" / "steps"
    if steps.is_dir():
        for p in sorted(steps.glob("*.py")):
            key = _rel(p)
            if key not in seen:
                seen.add(key)
                found.append(p)
    return found


def build_index(*, write: bool = True) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in _iter_source_files():
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("knowledge_index: skip {}: {}", path, e)
            continue
        rel = _rel(path)
        title = _title_from_text(raw, Path(rel).name)
        entry = {
            "path": rel,
            "title": title,
            "excerpt": _excerpt(raw),
            "tags": _tags_for(rel, raw),
            "size": len(raw),
        }
        entries.append(entry)
    payload = {
        "version": 1,
        "root": str(_REPO_ROOT),
        "count": len(entries),
        "entries": entries,
    }
    if write:
        dest = index_path()
        dest.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("knowledge_index: wrote {} entries → {}", len(entries), dest)
    return payload


def load_index(*, rebuild_if_missing: bool = True) -> dict[str, Any]:
    path = index_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("entries"), list):
                return data
        except Exception as e:  # noqa: BLE001
            logger.warning("knowledge_index: bad file {}: {}", path, e)
    if rebuild_if_missing:
        return build_index(write=True)
    return {"version": 1, "count": 0, "entries": []}


def _score(query: str, entry: dict[str, Any]) -> int:
    q = (query or "").strip().lower()
    if not q:
        return 0
    tokens = [t for t in _TOKEN_RE.findall(q) if len(t) >= 2]
    if not tokens:
        tokens = [q]
    blob = " ".join(
        [
            str(entry.get("path") or ""),
            str(entry.get("title") or ""),
            str(entry.get("excerpt") or ""),
            " ".join(entry.get("tags") or []),
        ]
    ).lower()
    score = 0
    for t in tokens:
        if t in blob:
            score += 3
            if t in str(entry.get("path") or "").lower():
                score += 5
            if t in str(entry.get("title") or "").lower():
                score += 4
            if t in [x.lower() for x in (entry.get("tags") or [])]:
                score += 2
    # whole phrase bonus
    if q in blob:
        score += 6
    return score


def search_knowledge(
    query: str,
    *,
    limit: int = 12,
    rebuild_if_missing: bool = True,
) -> list[dict[str, Any]]:
    data = load_index(rebuild_if_missing=rebuild_if_missing)
    entries = data.get("entries") or []
    scored: list[tuple[int, dict[str, Any]]] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        s = _score(query, e)
        if s > 0:
            scored.append((s, e))
    scored.sort(key=lambda x: (-x[0], str(x[1].get("path") or "")))
    out: list[dict[str, Any]] = []
    for s, e in scored[: max(1, min(limit, 50))]:
        item = dict(e)
        item["score"] = s
        out.append(item)
    return out
