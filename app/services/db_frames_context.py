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


def _camera_subdivide_from(obj: Any) -> dict[str, Any]:
    attrs = getattr(obj, "attrs", None)
    if attrs is None and isinstance(obj, dict):
        attrs = obj.get("attrs")
    if isinstance(attrs, dict):
        raw = attrs.get("camera_subdivide")
        if isinstance(raw, dict):
            return raw
    return {}


def _shot_index_from(obj: Any) -> int:
    try:
        return max(1, int(_camera_subdivide_from(obj).get("shot_index") or 1))
    except (TypeError, ValueError):
        return 1


def _frame_uuid(obj: Any) -> str:
    if isinstance(obj, dict):
        return str(obj.get("uuid") or "").strip()
    return str(getattr(obj, "uuid", None) or "").strip()


def _frame_vo(obj: Any) -> str:
    if isinstance(obj, dict):
        return str(obj.get("voiceover_text") or obj.get("закадр") or "").strip()
    return str(getattr(obj, "voiceover_text", None) or "").strip()


def collapse_script_writer_frames(
    frames: list[Any],
    rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Сценарист: пачка для GPT = 1 VO-ячейка (uuid родителя + полный закадр).

    Только вход модели: не режем и не пишем фрагменты шотов. После camera_expand
    дети уже несут свои куски; если отдать все кадры, будет ~188 пачек вместо
    ~66 ячеек.
    """
    by_uuid_row = {
        str(r.get("uuid") or "").strip(): dict(r)
        for r in (rows or [])
        if str(r.get("uuid") or "").strip()
    }
    groups: dict[str, list[Any]] = {}
    order: list[str] = []
    for fr in frames:
        uid = _frame_uuid(fr)
        if not uid:
            continue
        parent = str(_camera_subdivide_from(fr).get("parent_uuid") or "").strip() or uid
        if parent not in groups:
            groups[parent] = []
            order.append(parent)
        groups[parent].append(fr)
    out: list[dict[str, Any]] = []
    for parent_uid in order:
        members = list(groups[parent_uid])
        members.sort(key=_shot_index_from)
        parent_fr = next(
            (m for m in members if _shot_index_from(m) <= 1),
            members[0],
        )
        parent_id = _frame_uuid(parent_fr)
        full = " ".join(_frame_vo(m) for m in members if _frame_vo(m))
        full = " ".join(full.split())
        if not full:
            continue
        row = dict(by_uuid_row.get(parent_id) or {})
        row["uuid"] = parent_id
        row["voiceover_text"] = full
        if "number" not in row:
            num = getattr(parent_fr, "number", None)
            if isinstance(parent_fr, dict):
                num = parent_fr.get("number", num)
            if num is not None:
                row["number"] = num
        out.append(row)
    return out


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
        img = str(getattr(fr, "image_prompt", None) or "").strip()
        if img:
            # Нужен ключ для skip_if_field=image_prompt, иначе retry
            # снова шлёт все 188 кадров.
            row["image_prompt"] = _clip(img, _ATTR_MAX)
        row.update(slim_attrs_for_excel_gpt(getattr(fr, "attrs", None)))
        rows.append(row)
    return {
        "source": "db_v2",
        "project_id": project_id,
        "slug": slug,
        "frames": rows,
        "characters": list(characters or []),
    }


def build_excel_gpt_check_context(
    *,
    project_id: int,
    slug: str,
    frames: list[Any],
    characters: list[dict[str, Any]],
    scene_registry: list[Any] | None = None,
) -> dict[str, Any]:
    """Снимок для checkMode db_check.json: slim attrs + scene_registry."""
    ctx = build_excel_gpt_db_context(
        project_id=project_id,
        slug=slug,
        frames=frames,
        characters=characters,
    )
    ctx["scene_registry"] = list(scene_registry or [])
    return ctx


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
    """Полный снимок кадра из DB для агента промтов картинок.

    SoT = База: uuid + закадр + meaning + scene_grammar attrs + animation_prompt
    (если уже есть). Батчинг режет по числу кадров — VO не выкидываем.
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
        vo = str(getattr(fr, "voiceover_text", None) or "").strip()
        if vo:
            row["voiceover_text"] = vo
        meaning = (getattr(fr, "meaning", None) or "") or ""
        if meaning.strip():
            row["meaning"] = meaning.strip()
        anim = str(getattr(fr, "animation_prompt", None) or "").strip()
        if anim:
            row["animation_prompt"] = anim
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
