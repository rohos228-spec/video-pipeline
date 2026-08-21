"""Безопасный merge project.meta с аудитом prompt-ключей."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

PROMPT_META_KEYS: frozenset[str] = frozenset(
    {"custom_prompts", "prompt_slot_variants", "prompt_history"}
)

# UI часто шлёт весь meta из React Query кэша. Пока воркер писал
# gpt_operator_results / storage_nodes — stale PATCH затирал их null/{} и
# ВСЕ ноды «теряли» результат. Эти ключи deep-merge + отказ от null wipe.
PROTECTED_META_BUCKETS: frozenset[str] = frozenset(
    {
        "gpt_operator_results",
        "excel_gpt_nodes",
        "storage_nodes",
        "xlsx_snapshots_by_node",
        "prompt_slot_variants",
        "custom_prompts",
        "prompt_history",
        "node_step_params",
        "excel_lane_bindings",
        "canvas_graph",
    }
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


def _canvas_node_count(raw: Any) -> int:
    if not isinstance(raw, dict):
        return 0
    nodes = raw.get("nodes")
    return len(nodes) if isinstance(nodes, list) else 0


def _merge_canvas_graph(base_val: Any, patch_val: Any) -> Any:
    """canvas_graph: не принимать пустой/урезанный граф из stale UI autosave."""
    if patch_val is None:
        return base_val
    if not isinstance(patch_val, dict):
        return patch_val
    base_n = _canvas_node_count(base_val)
    patch_n = _canvas_node_count(patch_val)
    if patch_n == 0 and base_n > 0:
        return dict(base_val) if isinstance(base_val, dict) else base_val
    # Урезало ≥30% нод — почти всегда stale cache, не autosave одной ноды.
    if base_n >= 8 and patch_n < int(base_n * 0.7):
        logger.warning(
            "project_meta: отказано в canvas_graph wipe {} → {} nodes",
            base_n,
            patch_n,
        )
        return dict(base_val) if isinstance(base_val, dict) else base_val
    return dict(patch_val)


def _merge_protected_bucket(base_val: Any, patch_val: Any) -> Any:
    """Deep-merge dict-бакета: null и пустой {} не затирают живые данные."""
    if patch_val is None:
        return base_val
    if not isinstance(patch_val, dict):
        return patch_val
    if not isinstance(base_val, dict):
        return dict(patch_val)
    if not patch_val and base_val:
        # stale UI прислал пустой bucket — оставляем серверное
        return dict(base_val)
    out = dict(base_val)
    for key, val in patch_val.items():
        if val is None:
            # null entry (часто после canvas PATCH) — не трогаем
            continue
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            # пустой {} не вычищает заполненный результат ноды
            if not val and out[key]:
                continue
            nested = dict(out[key])
            for nk, nv in val.items():
                if nv is not None:
                    nested[nk] = nv
            out[key] = nested
        else:
            out[key] = val
    return out


def merge_project_meta(
    existing: dict | None,
    patch: dict | None,
    *,
    source: str,
    project_id: int | None = None,
) -> dict:
    """Shallow-merge patch в existing meta; protected buckets — deep + anti-wipe."""
    base = dict(existing or {})
    if not isinstance(patch, dict):
        return base
    merged = {**base, **patch}
    for key in PROTECTED_META_BUCKETS:
        if key not in patch:
            continue
        if key == "canvas_graph":
            merged[key] = _merge_canvas_graph(base.get(key), patch.get(key))
            continue
        merged[key] = _merge_protected_bucket(base.get(key), patch.get(key))
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


def set_meta_fields(project: Any, **fields: Any) -> dict[str, Any]:
    """Точечно обновить ключи meta без затирания свежего canvas_graph.

    Воркер часто держит project.meta минутами (GPT). Shallow ``dict(meta)`` +
    flush поверх чужого PATCH канваса снимает стрелки (20→19 edges) — storage
    и topic «отваливаются», voiceover потом внезапно появляется через autoSync.
    """
    meta = dict(project.meta or {}) if isinstance(project.meta, dict) else {}
    for key, value in fields.items():
        if value is None:
            meta.pop(key, None)
        else:
            meta[key] = value
    project.meta = meta
    return meta
