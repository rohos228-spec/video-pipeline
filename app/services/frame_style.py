"""Нода «Стиль» группы script_frames_qc (``n_excel_gpt_fw_style``).

Код, без LLM. Берёт выбранный файл стиля из
``templates/node_groups/script_frames_qc/styles/style_*.md`` и дописывает
его после сцены в промт ГЛАВНОГО кадра: K1 (``parent_id`` пустой) в
``кадры[]`` VO-родителя. Дочерние шоты не трогает — их PNG собирается
с PNG родителя.

Пишет:
- ``кадры[K1].<поле>`` = сцена + стиль (поле, откуда взята сцена:
  ``промт_картинки`` или ``промт_старт``);
- ``кадры[K1].промт_без_стиля`` — исходная сцена без стиля;
- ``кадры[K1].стиль`` — имя варианта стиля;
- ``frames.image_prompt`` VO-родителя = тот же итоговый промт (его читает
  Outsee).

Повторный прогон и смена стиля не дублируют стиль: сцена берётся из
``промт_без_стиля`` (иначе ``промт_картинки`` → ``промт_старт``), хвост
STYLE / Final style lock / Negative срезается.

Лимит итогового промта — ``OUTSEE_PROMPT_TARGET_BODY_CHARS`` (4877):
режем сцену по границе предложения, стиль не режем никогда. Стиль сам по
себе длиннее ``limit - MIN_SCENE_BUDGET`` — ошибка.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

STYLE_DIR_NAME = "styles"
STYLE_PREFIX = "style_"
DEFAULT_STYLE = "style_noir_watercolour_16_09"
STYLE_GROUP_ID = "script_frames_qc"

SCENE_RAW_KEY = "промт_без_стиля"
STYLE_NAME_KEY = "стиль"
STYLE_FIELD_KEY = "стиль_поле"
MAIN_PROMPT_KEY = "промт_картинки"
START_PROMPT_KEY = "промт_старт"

MIN_SCENE_BUDGET = 200
SEPARATOR = "\n\n"

_SENTENCE_END_RE = re.compile(r"[.!?…;](?=\s|$)")


class StyleError(ValueError):
    """Стиль не найден / пустой / не влезает в лимит."""


def body_limit() -> int:
    from app.generation_options import OUTSEE_PROMPT_TARGET_BODY_CHARS

    return int(OUTSEE_PROMPT_TARGET_BODY_CHARS)


def max_style_chars(limit: int | None = None) -> int:
    lim = body_limit() if limit is None else int(limit)
    return lim - MIN_SCENE_BUDGET


def styles_dir() -> Path:
    from app.project_root import find_project_root

    return (
        find_project_root()
        / "templates"
        / "node_groups"
        / STYLE_GROUP_ID
        / STYLE_DIR_NAME
    )


def clean_style_name(name: str | None) -> str:
    clean = str(name or "").strip()
    if clean.lower().endswith(".md"):
        clean = clean[:-3].rstrip()
    return clean


def is_style_name(name: str | None) -> bool:
    clean = clean_style_name(name)
    return bool(clean) and clean.startswith(STYLE_PREFIX) and "/" not in clean and "\\" not in clean


def list_style_names() -> list[str]:
    d = styles_dir()
    if not d.is_dir():
        return []
    return sorted(
        p.stem for p in d.glob(f"{STYLE_PREFIX}*.md") if p.is_file()
    )


def style_path(name: str | None) -> Path | None:
    clean = clean_style_name(name)
    if not is_style_name(clean):
        return None
    p = styles_dir() / f"{clean}.md"
    return p if p.is_file() else None


def normalize_style_text(text: str) -> str:
    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    raw = re.sub(r"\n[ \t]*\n(?:[ \t]*\n)+", "\n\n", raw)
    return raw.strip()


def load_style_text(name: str | None, *, limit: int | None = None) -> str:
    p = style_path(name)
    if p is None:
        raise StyleError(f"стиль не найден: {name!r} (папка {styles_dir()})")
    text = normalize_style_text(p.read_text(encoding="utf-8"))
    if not text:
        raise StyleError(f"стиль пустой: {p}")
    cap = max_style_chars(limit)
    if len(text) > cap:
        raise StyleError(
            f"стиль {clean_style_name(name)} = {len(text)} симв > {cap} "
            f"(лимит промта {body_limit() if limit is None else limit} "
            f"минус {MIN_SCENE_BUDGET} на сцену)"
        )
    return text


def strip_style(text: str | None) -> str:
    """Сцена без хвоста STYLE / Final style lock / Negative."""
    from app.services.img_pr_style import split_style_lock

    raw = (text or "").replace("\r\n", "\n").strip()
    if not raw:
        return ""
    scene, _style = split_style_lock(raw)
    return scene.strip()


def trim_scene(scene: str, budget: int) -> tuple[str, bool]:
    """Обрезать сцену до ``budget`` по границе предложения. (текст, обрезано?)"""
    text = (scene or "").strip()
    if budget <= 0:
        return "", bool(text)
    if len(text) <= budget:
        return text, False
    head = text[:budget]
    best = -1
    for m in _SENTENCE_END_RE.finditer(head):
        best = m.end()
    if best >= max(1, int(budget * 0.5)):
        cut = head[:best]
    else:
        ws = head.rfind(" ")
        cut = head[:ws] if ws > 0 else head
    cut = cut.rstrip(" ,;:—-\n\t")
    return cut[:budget], True


def compose_styled_prompt(
    scene: str, style: str, *, limit: int | None = None
) -> tuple[str, str, bool]:
    """(итоговый промт, сцена после обрезки, обрезано?)."""
    lim = body_limit() if limit is None else int(limit)
    style_text = (style or "").strip()
    if not style_text:
        raise StyleError("пустой стиль")
    budget = lim - len(style_text) - len(SEPARATOR)
    if budget < MIN_SCENE_BUDGET:
        raise StyleError(
            f"стиль {len(style_text)} симв: на сцену остаётся {budget} < {MIN_SCENE_BUDGET}"
        )
    scene_cut, trimmed = trim_scene(scene, budget)
    if not scene_cut:
        return style_text, "", trimmed
    final = f"{scene_cut}{SEPARATOR}{style_text}"
    return final, scene_cut, trimmed


def _attrs(frame: Any) -> dict[str, Any]:
    raw = getattr(frame, "attrs", None)
    return raw if isinstance(raw, dict) else {}


def _role(frame: Any) -> str:
    cs = _attrs(frame).get("camera_subdivide")
    return str((cs or {}).get("role") or "") if isinstance(cs, dict) else ""


def is_main_frame_row(frame: Any) -> bool:
    """VO-родитель (не дочерний шот, не leftover) с кадрами."""
    cs = _attrs(frame).get("camera_subdivide")
    if isinstance(cs, dict) and cs.get("leftover"):
        return False
    if _role(frame) == "shot":
        return False
    kadry = _attrs(frame).get("кадры")
    return isinstance(kadry, list) and any(isinstance(x, dict) for x in kadry)


def master_index(kadry: list[Any]) -> int | None:
    """Индекс K1: первый кадр с пустым parent_id, иначе первый dict."""
    first: int | None = None
    for i, x in enumerate(kadry or []):
        if not isinstance(x, dict):
            continue
        if first is None:
            first = i
        if x.get("parent_id") in (None, "", "null"):
            return i
    return first


def scene_source(entry: dict[str, Any]) -> tuple[str, str, str]:
    """(сцена без стиля, ключ-источник, поле для записи)."""
    field = str(entry.get(STYLE_FIELD_KEY) or "").strip()
    raw = str(entry.get(SCENE_RAW_KEY) or "").strip()
    if raw:
        if field not in (MAIN_PROMPT_KEY, START_PROMPT_KEY):
            field = (
                MAIN_PROMPT_KEY
                if str(entry.get(MAIN_PROMPT_KEY) or "").strip()
                or not str(entry.get(START_PROMPT_KEY) or "").strip()
                else START_PROMPT_KEY
            )
        return strip_style(raw), SCENE_RAW_KEY, field
    main = str(entry.get(MAIN_PROMPT_KEY) or "").strip()
    if main:
        return strip_style(main), MAIN_PROMPT_KEY, MAIN_PROMPT_KEY
    start = str(entry.get(START_PROMPT_KEY) or "").strip()
    if start:
        return strip_style(start), START_PROMPT_KEY, START_PROMPT_KEY
    return "", "", ""


def _flag(frame: Any) -> None:
    try:
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(frame, "attrs")
    except Exception:  # noqa: BLE001 — простые объекты в тестах
        pass


def apply_style_to_frames(
    frames: list[Any],
    *,
    style_name: str,
    style_text: str,
    limit: int | None = None,
    set_ready_status: bool = True,
) -> dict[str, Any]:
    """Дописать стиль к промтам главных кадров. Возвращает отчёт."""
    lim = body_limit() if limit is None else int(limit)
    style_clean = (style_text or "").strip()
    report: dict[str, Any] = {
        "style": clean_style_name(style_name),
        "style_chars": len(style_clean),
        "limit": lim,
        "scene_budget": lim - len(style_clean) - len(SEPARATOR),
        "counts": {
            "frames_total": len(frames),
            "main_rows": 0,
            "styled": 0,
            "unchanged": 0,
            "trimmed": 0,
            "no_prompt": 0,
            "skipped_shot_rows": 0,
            "image_prompt_written": 0,
            "source_промт_без_стиля": 0,
            "source_промт_картинки": 0,
            "source_промт_старт": 0,
        },
        "styled": [],
        "trimmed": [],
        "no_prompt": [],
    }
    counts = report["counts"]
    try:
        from app.models import FrameStatus
    except Exception:  # noqa: BLE001
        FrameStatus = None  # type: ignore[assignment]

    for fr in frames:
        if not is_main_frame_row(fr):
            counts["skipped_shot_rows"] += 1
            continue
        counts["main_rows"] += 1
        attrs = dict(_attrs(fr))
        kadry = [dict(x) if isinstance(x, dict) else x for x in (attrs.get("кадры") or [])]
        idx = master_index(kadry)
        num = getattr(fr, "number", None)
        uid = str(getattr(fr, "uuid", "") or "")
        if idx is None:
            counts["no_prompt"] += 1
            report["no_prompt"].append({"number": num, "uuid": uid, "k1": None})
            continue
        entry = kadry[idx]
        scene, src, field = scene_source(entry)
        if not scene:
            counts["no_prompt"] += 1
            report["no_prompt"].append(
                {"number": num, "uuid": uid, "k1": entry.get("id")}
            )
            continue
        counts[f"source_{src}"] += 1
        final, scene_cut, trimmed = compose_styled_prompt(
            scene, style_clean, limit=lim
        )
        before_field = str(entry.get(field) or "")
        before_ip = str(getattr(fr, "image_prompt", None) or "")
        entry[field] = final
        entry[SCENE_RAW_KEY] = scene
        entry[STYLE_NAME_KEY] = report["style"]
        entry[STYLE_FIELD_KEY] = field
        kadry[idx] = entry
        attrs["кадры"] = kadry
        changed = before_field != final or before_ip != final
        fr.attrs = attrs
        _flag(fr)
        if before_ip != final:
            fr.image_prompt = final
        counts["image_prompt_written"] += 1
        if (
            set_ready_status
            and FrameStatus is not None
            and getattr(fr, "status", None) is FrameStatus.planned
        ):
            fr.status = FrameStatus.image_prompt_ready
        row = {
            "number": num,
            "uuid": uid,
            "k1": entry.get("id"),
            "field": field,
            "source": src,
            "scene_chars": len(scene),
            "scene_used_chars": len(scene_cut),
            "final_chars": len(final),
            "trimmed": trimmed,
        }
        if changed:
            counts["styled"] += 1
        else:
            counts["unchanged"] += 1
        report["styled"].append(row)
        if trimmed:
            counts["trimmed"] += 1
            report["trimmed"].append(row)
    return report
