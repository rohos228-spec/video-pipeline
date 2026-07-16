"""Безопасный merge project.meta с аудитом prompt-ключей."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

PROMPT_META_KEYS: frozenset[str] = frozenset(
    {"custom_prompts", "prompt_slot_variants", "prompt_history"}
)

PROMPTS_AUDIT_LOG = Path("logs/prompts_audit.log")


def _prompt_key_count(meta: dict | None, key: str) -> int:
    if not isinstance(meta, dict):
        return 0
    val = meta.get(key)
    if isinstance(val, dict):
        return len(val)
    if isinstance(val, list):
        return len(val)
    return 0 if val is None else 1


def _write_prompts_audit(line: str) -> None:
    try:
        PROMPTS_AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with PROMPTS_AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        logger.warning("prompts_audit: cannot write {}: {}", PROMPTS_AUDIT_LOG, e)


def audit_prompt_meta_change(
    *,
    source: str,
    project_id: int | None,
    before: dict | None,
    after: dict | None,
) -> None:
    """Логировать уменьшение/удаление prompt-ключей."""
    pid = project_id if project_id is not None else "?"
    ts = datetime.utcnow().isoformat(timespec="seconds")
    for key in PROMPT_META_KEYS:
        n_before = _prompt_key_count(before, key)
        n_after = _prompt_key_count(after, key)
        if n_after < n_before or (n_before and key not in (after or {})):
            line = (
                f"{ts}\tsource={source}\tproject={pid}\tkey={key}\t"
                f"keys {n_before} → {n_after}"
            )
            _write_prompts_audit(line)
            logger.warning("prompts_audit: {}", line)


def merge_project_meta(
    existing: dict | None,
    patch: dict | None,
    *,
    source: str,
    project_id: int | None = None,
) -> dict:
    """Shallow-merge patch в existing meta; аудит prompt-ключей."""
    base = dict(existing or {})
    if not isinstance(patch, dict):
        return base
    merged = {**base, **patch}
    audit_prompt_meta_change(
        source=source,
        project_id=project_id,
        before=base,
        after=merged,
    )
    return merged


def apply_project_meta_patch(
    project: Any,
    patch: dict | None,
    *,
    source: str,
) -> None:
    """project.meta = merge(existing, patch) с аудитом."""
    project.meta = merge_project_meta(
        project.meta if isinstance(project.meta, dict) else {},
        patch,
        source=source,
        project_id=getattr(project, "id", None),
    )
