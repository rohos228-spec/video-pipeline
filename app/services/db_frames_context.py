"""Компактный снимок DB для GPT (db_frames.json).

Используется шагом img_pr и при необходимости excel_gpt project_file.
Ключи attrs — канон после scene_grammar expand (английские).
"""

from __future__ import annotations

from typing import Any

# Поля постановки кадра после scene_grammar v1.6 (whitelist — не тащим весь attrs).
_IMG_PR_ATTR_KEYS: tuple[str, ...] = (
    "place",
    "accent",
    "scene_sense",
    "visual_type",
    "scene_feature",
    "main_action",
    "lighting",
    "scene_lighting",
    "characters",
    "cluster",
    "scene_structure",
    "edit_type",
    "scene_transition",
    "shot01_bg",
    "shot01_props",
    "shot01_action",
    "shot01_description",
    "shot01_notes",
    "shot01_transition",
    "shot02_bg",
    "shot02_props",
    "shot02_action",
    "shot02_description",
    "shot02_characters",
    "shot02_notes",
    "shot02_transition",
)


def _pick_attrs(attrs: dict[str, Any] | None) -> dict[str, str]:
    src = attrs if isinstance(attrs, dict) else {}
    out: dict[str, str] = {}
    for key in _IMG_PR_ATTR_KEYS:
        val = src.get(key)
        if val is None:
            continue
        text = str(val).strip()
        if text:
            out[key] = text
    # Русский алиас для персонажей кадра (агенты часто ждут «персонажи»).
    if "characters" in out and "персонажи" not in out:
        out["персонажи"] = out["characters"]
    elif not out.get("characters"):
        for alt in ("персонажи", "persons"):
            raw = src.get(alt)
            if raw is not None and str(raw).strip():
                out["characters"] = str(raw).strip()
                out["персонажи"] = out["characters"]
                break
    return out


_EXCEL_GPT_VO_MAX = 400
_ATTR_MAX = 500


def _clip(text: str, n: int) -> str:
    t = text.strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def slim_attrs_for_excel_gpt(attrs: dict[str, Any] | None) -> dict[str, str]:
    picked = _pick_attrs(attrs)
    return {k: _clip(v, _ATTR_MAX) for k, v in picked.items()}


def build_excel_gpt_db_context(
    *,
    project_id: int,
    slug: str,
    frames: list[Any],
    characters: list[dict[str, str]],
) -> dict[str, Any]:
    """Снимок для excel_gpt: whitelist attrs + короткий закадр, без сырого attrs."""
    rows: list[dict[str, Any]] = []
    for fr in frames:
        uuid = str(getattr(fr, "uuid", None) or "").strip()
        if not uuid:
            continue
        row: dict[str, Any] = {"number": getattr(fr, "number", None), "uuid": uuid}
        vo = str(getattr(fr, "voiceover_text", None) or "")
        if vo.strip():
            row["voiceover_text"] = _clip(vo, _EXCEL_GPT_VO_MAX)
        meaning = str(getattr(fr, "meaning", None) or "").strip()
        if meaning:
            row["meaning"] = _clip(meaning, _ATTR_MAX)
        row.update(slim_attrs_for_excel_gpt(getattr(fr, "attrs", None)))
        rows.append(row)
    return {
        "source": "db_v2",
        "project_id": project_id,
        "slug": slug,
        "frames": rows,
        "characters": list(characters or []),
    }


def build_img_pr_db_context(
    *,
    project_id: int,
    slug: str,
    frames: list[Any],
    characters: list[dict[str, str]],
    general_plan: str = "",
    include_characters: bool = True,
    include_field_map: bool = False,
) -> dict[str, Any]:
    """Снимок для агента промтов картинок (DB SoT после scene_grammar).

    Без voiceover_text — он раздувает JSON и режется лимитом API (60k).
    """
    frame_rows: list[dict[str, Any]] = []
    for fr in frames:
        uuid = getattr(fr, "uuid", None) or ""
        if not str(uuid).strip():
            continue
        row: dict[str, Any] = {
            "number": getattr(fr, "number", None),
            "uuid": str(uuid),
        }
        meaning = (getattr(fr, "meaning", None) or "") or ""
        if meaning.strip():
            row["meaning"] = meaning.strip()
        picked = _pick_attrs(getattr(fr, "attrs", None))
        row.update(picked)
        frame_rows.append(row)
    out: dict[str, Any] = {
        "source": "db_v2",
        "project_id": project_id,
        "slug": slug,
        "frames": frame_rows,
    }
    gp = (general_plan or "").strip()
    if gp:
        # Короткий кусок — эпоха, не простыня.
        out["general_plan"] = gp[:800]
    if include_characters:
        out["characters"] = list(characters or [])
    if include_field_map:
        out["field_map"] = {
            "place": "SETTING",
            "lighting|scene_lighting": "LIGHT",
            "shot01_bg": "BG",
            "shot01_action": "ACTION",
            "shot01_description": "CAMERA",
            "accent": "FOCAL",
            "scene_sense": "visible facts",
            "scene_feature": "shot scale",
        }
    return out
