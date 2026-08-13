"""Allowlist полей apply-ops по типу ноды + покрытие uuid N/N.

Писатель в БД один: ``db_apply.apply_ops``. Этот модуль решает, *какие*
поля нода имеет право отдать в apply, и достаточно ли uuid после merge.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from app.services.db_apply import FIELD_ALIASES

PROMPT_FIELDS = frozenset(
    {
        "image_prompt",
        "animation_prompt",
        "image_prompt_shot2",
        "animation_prompt_shot2",
    }
)

IMAGE_PROMPT_FIELDS = frozenset({"image_prompt", "image_prompt_shot2"})
ANIM_PROMPT_FIELDS = frozenset({"animation_prompt", "animation_prompt_shot2"})
CHARACTER_FIELDS = frozenset({"characters"})

_NODE_KINDS = frozenset(
    {"img_pr", "anim_pr", "excel_gpt", "excel_gpt_no_prompts"}
)


def _norm_key(raw: str) -> str:
    return str(raw).strip().lower().replace(" ", "_")


def _canon_field(raw: str) -> str | None:
    return FIELD_ALIASES.get(_norm_key(raw))


def _alias_set(canons: frozenset[str]) -> frozenset[str]:
    names = set(canons)
    for alias, canon in FIELD_ALIASES.items():
        if canon in canons:
            names.add(alias)
            names.add(_norm_key(alias))
    return frozenset(names)


_PROMPT_KEYS = _alias_set(PROMPT_FIELDS)
_IMG_PR_KEYS = _alias_set(IMAGE_PROMPT_FIELDS) | _alias_set(CHARACTER_FIELDS)
_ANIM_PR_KEYS = _alias_set(ANIM_PROMPT_FIELDS)


def _keep_field(key: str, node_kind: str) -> bool:
    norm = _norm_key(key)
    canon = _canon_field(key)
    if node_kind in ("excel_gpt", "excel_gpt_no_prompts"):
        if canon in PROMPT_FIELDS or norm in _PROMPT_KEYS:
            return False
        return True
    if node_kind == "img_pr":
        return (
            canon in IMAGE_PROMPT_FIELDS
            or canon in CHARACTER_FIELDS
            or norm in _IMG_PR_KEYS
        )
    if node_kind == "anim_pr":
        return canon in ANIM_PROMPT_FIELDS or norm in _ANIM_PR_KEYS
    return True


def count_prompt_fields(ops: list[dict[str, Any]] | None) -> int:
    """Сколько prompt-полей несут ops (для аудита обходов контракта)."""
    n = 0
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields")
        if not isinstance(fields, dict):
            continue
        for key in fields:
            canon = _canon_field(str(key))
            if canon in PROMPT_FIELDS or _norm_key(str(key)) in _PROMPT_KEYS:
                n += 1
    return n


def filter_ops_for_node(
    ops: list[dict[str, Any]] | None,
    *,
    node_kind: str,
) -> list[dict[str, Any]]:
    """Скопировать ops и выкинуть поля, которые этой ноде писать нельзя."""
    kind = str(node_kind or "").strip()
    if not kind:
        return [dict(op) if isinstance(op, dict) else op for op in (ops or [])]
    if kind not in _NODE_KINDS:
        raise ValueError(f"unknown node_kind {node_kind!r}")

    out: list[dict[str, Any]] = []
    for op in ops or []:
        if not isinstance(op, dict):
            continue
        new_op = dict(op)
        fields = op.get("fields")
        if isinstance(fields, dict):
            kept = {k: v for k, v in fields.items() if _keep_field(str(k), kind)}
            new_op["fields"] = kept
            if str(new_op.get("target") or "frame") == "frame" and not kept:
                continue
        out.append(new_op)
    return out


@dataclass(frozen=True)
class Coverage:
    matched: list[str]
    missing: list[str]
    extra: list[str]
    ok: bool


def _unique_uuids(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        u = str(raw or "").strip()
        if u and u not in seen:
            out.append(u)
            seen.add(u)
    return out


def coverage_report(
    ops: list[dict[str, Any]] | None,
    expected_uuids: list[str] | None,
) -> Coverage:
    """N/N по frame_uuid. extra не ломает ok, если все expected на месте."""
    expected = _unique_uuids(expected_uuids or [])
    got = _unique_uuids(
        (op.get("frame_uuid") if isinstance(op, dict) else None) for op in (ops or [])
    )
    expected_set = set(expected)
    got_set = set(got)
    matched = [u for u in expected if u in got_set]
    missing = [u for u in expected if u not in got_set]
    extra = [u for u in got if u not in expected_set]
    ok = bool(expected) and not missing
    return Coverage(matched=matched, missing=missing, extra=extra, ok=ok)


def skip_frame_coverage(
    *,
    scene_grammar: bool,
    character_registry: bool,
    ops_list,
    chars_list,
    scenes_list,
) -> bool:
    """Skip N/N frame_uuid gate for entity-card nodes (scene_grammar, character_registry)."""
    return bool(
        (scene_grammar or character_registry)
        and (chars_list or scenes_list or character_registry)
        and (not ops_list or character_registry)
    )


def precheck_coverage(
    ops: list[dict[str, Any]] | None,
    expected_uuids: list[str],
    *,
    repair_near_miss: bool = True,
) -> Coverage:
    """Гейт ДО ``apply_ops``: near-miss repair (in-place) + покрытие N/N.

    Вызывается до любой записи в БД — частичный apply не должен
    фиксироваться, иначе retry стартует на уже испорченных данных.
    """
    from app.services.db_apply import repair_near_miss_frame_uuids

    frame_ops = [
        op
        for op in (ops or [])
        if isinstance(op, dict) and op.get("target", "frame") == "frame"
    ]
    if repair_near_miss and expected_uuids:
        repair_near_miss_frame_uuids(frame_ops, expected_uuids)
    return coverage_report(ops, expected_uuids=expected_uuids)
