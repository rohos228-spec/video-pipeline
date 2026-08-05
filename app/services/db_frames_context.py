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


def build_img_pr_db_context(
    *,
    project_id: int,
    slug: str,
    frames: list[Any],
    characters: list[dict[str, str]],
    general_plan: str = "",
) -> dict[str, Any]:
    """Снимок для агента промтов картинок (DB SoT после scene_grammar)."""
    frame_rows: list[dict[str, Any]] = []
    for fr in frames:
        uuid = getattr(fr, "uuid", None) or ""
        if not str(uuid).strip():
            continue
        row: dict[str, Any] = {
            "number": getattr(fr, "number", None),
            "uuid": str(uuid),
            "voiceover_text": (getattr(fr, "voiceover_text", None) or "") or "",
            "meaning": (getattr(fr, "meaning", None) or "") or "",
        }
        picked = _pick_attrs(getattr(fr, "attrs", None))
        row.update(picked)
        frame_rows.append(row)
    return {
        "source": "db_v2",
        "project_id": project_id,
        "slug": slug,
        "general_plan": (general_plan or "").strip(),
        "frames": frame_rows,
        "characters": list(characters or []),
        "field_map": {
            "place": "место / SETTING",
            "lighting|scene_lighting": "свет сцены → NOIR_LIGHTING (те же источники)",
            "shot01_bg": "фон кадра → деталь SETTING",
            "shot01_action": "видимое действие → ACTION",
            "shot01_description": "камера/композиция → CAMERA (не выдумывай план)",
            "shot01_props": "предметы (только из списка)",
            "accent": "визуальный центр → FOCAL_POINT",
            "scene_sense": "видимый факт сцены (не литература, не закадр)",
            "scene_feature": "крупность/роль шота (Общий/Средний/…)",
            "visual_type": "тон сцены; STYLE LOCK важнее (не уходи в фотореализм)",
            "characters|персонажи": "коды c01… из Entity",
        },
    }
