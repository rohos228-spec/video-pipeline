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
# Действие кадра — группа script_frames_qc (fw_frames), не закадр.
ACTION_FIELDS = frozenset({"shot01_action", "main_action"})
# Меню съёмки: fw_frames пишет вместе с промтами (footer prompts + добор).
CAMERA_MENU_FIELDS = frozenset({"крупность", "движение", "набор", "camera_subdivide"})
# Закадр пишет только split / n_script / человек. excel_gpt не генерирует текст.
VO_FIELDS = frozenset({"voiceover_text", "meaning"})
# Сценарист (fw_script): только смысловые биты на seed-ячейке.
BITS_FIELDS = frozenset({"биты"})

_NODE_KINDS = frozenset(
    {
        "img_pr",
        "anim_pr",
        "excel_gpt",
        "excel_gpt_no_prompts",
        "excel_gpt_prompts",
        "excel_gpt_script",
    }
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
_VO_KEYS = _alias_set(VO_FIELDS)
_ACTION_KEYS = _alias_set(ACTION_FIELDS)
_CAMERA_MENU_KEYS = _alias_set(CAMERA_MENU_FIELDS)
_BITS_KEYS = _alias_set(BITS_FIELDS)
_IMG_PR_KEYS = _alias_set(IMAGE_PROMPT_FIELDS) | _alias_set(CHARACTER_FIELDS)
_ANIM_PR_KEYS = _alias_set(ANIM_PROMPT_FIELDS)


def _keep_field(key: str, node_kind: str) -> bool:
    norm = _norm_key(key)
    canon = _canon_field(key)
    if node_kind == "excel_gpt_script":
        return canon in BITS_FIELDS or norm in _BITS_KEYS
    if node_kind == "excel_gpt_prompts":
        return (
            canon in PROMPT_FIELDS
            or canon in ACTION_FIELDS
            or canon in CAMERA_MENU_FIELDS
            or norm in _PROMPT_KEYS
            or norm in _ACTION_KEYS
            or norm in _CAMERA_MENU_KEYS
        )
    if node_kind in ("excel_gpt", "excel_gpt_no_prompts"):
        if canon in PROMPT_FIELDS or norm in _PROMPT_KEYS:
            return False
        if canon in VO_FIELDS or norm in _VO_KEYS:
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
        if (
            kind.startswith("excel_gpt")
            and str(new_op.get("target") or "") == "replace_frames"
        ):
            # Разбивка — шаг split / camera_expand, не excel_gpt.
            continue
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
