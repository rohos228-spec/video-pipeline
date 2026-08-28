"""Шаблоны сцена → кадры (T0–T10, X1/X2) для ноды script_frames_qc / shots."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.project_root import find_project_root

_REL = Path("templates") / "shot_templates" / "shot_templates.json"


def shot_templates_path() -> Path:
    return find_project_root() / _REL


@lru_cache(maxsize=1)
def load_shot_templates() -> dict[str, Any]:
    path = shot_templates_path()
    if not path.is_file():
        return {
            "version": 0,
            "select": [],
            "templates": [],
            "shots": [],
            "rules_ai": [],
            "chains": [],
        }
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def clear_shot_templates_cache() -> None:
    load_shot_templates.cache_clear()


def format_shot_templates_catalog(*, max_chars: int = 12000) -> str:
    """Компактный каталог для accompanying ноды shots."""
    data = load_shot_templates()
    lines: list[str] = [
        "# ШАБЛОНЫ СЦЕНА→КАДРЫ (SoT)",
        "T = тип сцены. X = стык со следующей ячейкой.",
        "K = кадр внутри типа. required=1 нельзя выкинуть.",
        "drop_order = порядок удаления при коротком закадре.",
        "axis: нет | keep_sides | look_chain — см. rules ниже.",
        "",
        "## select (первое совпадение)",
    ]
    for row in data.get("select") or []:
        lines.append(
            f"{row.get('step')}. if={row.get('if')} → {row.get('then_template')}"
        )
    lines.append("")
    lines.append("## templates")
    for row in data.get("templates") or []:
        lines.append(
            f"{row.get('id')} | {row.get('name')} | when={row.get('when')} | "
            f"axis={row.get('axis')} | shots={row.get('shots_full')} | "
            f"drop={row.get('drop_order') or '—'} | parent={row.get('parent_default')}"
        )
    lines.append("")
    lines.append("## shots (строка кадра)")
    lines.append(
        "template_id|shot_id|n|plan|angle|place|who|action|parent|role|required"
    )
    for row in data.get("shots") or []:
        lines.append(
            f"{row.get('template_id')}|{row.get('shot_id')}|{row.get('n')}|"
            f"{row.get('plan')}|{row.get('angle')}|{row.get('place')}|"
            f"{row.get('who')}|{row.get('action')}|{row.get('parent')}|"
            f"{row.get('role')}|{row.get('required')}"
        )
    lines.append("")
    lines.append("## rules_ai")
    for row in data.get("rules_ai") or []:
        lines.append(
            f"[{row.get('тема')}={row.get('значение')}] {row.get('команда_для_ии')}"
        )
    text = "\n".join(lines).strip() + "\n"
    if len(text) > max_chars:
        return text[: max_chars - 20].rstrip() + "\n…(обрезано)\n"
    return text


def neighbor_place_hints(frames: list[Any]) -> str:
    """Для X1/X2: место предыдущей ячейки в батче (по number)."""
    rows: list[tuple[int, str, str, str]] = []
    for fr in frames:
        uid = str(getattr(fr, "uuid", None) or "").strip()
        if not uid and isinstance(fr, dict):
            uid = str(fr.get("uuid") or "").strip()
        if not uid:
            continue
        num = getattr(fr, "number", None)
        if num is None and isinstance(fr, dict):
            num = fr.get("number")
        try:
            n = int(num)
        except (TypeError, ValueError):
            continue
        attrs = getattr(fr, "attrs", None)
        if attrs is None and isinstance(fr, dict):
            attrs = fr.get("attrs") or fr
        attrs = attrs if isinstance(attrs, dict) else {}
        action = str(
            attrs.get("главное_действие")
            or attrs.get("main_action")
            or ""
        ).strip()
        place = ""
        for line in action.splitlines():
            line = line.strip()
            if not line or line.startswith("("):
                continue
            # «1. двор — действие» / «двор — действие»
            body = line.split(".", 1)[-1].strip() if line[:1].isdigit() else line
            if "—" in body:
                place = body.split("—", 1)[0].strip()
            elif "-" in body:
                place = body.split("-", 1)[0].strip()
            if place:
                break
        rows.append((n, uid, place, action[:120]))
    rows.sort(key=lambda r: r[0])
    if not rows:
        return ""
    lines = [
        "# СОСЕДИ ДЛЯ X1/X2",
        "X1 = следующая ячейка, то же место → parent = master прошлой локации, без нового ОБЩЕГО.",
        "X2 = другая география → parent_id null.",
    ]
    for i, (n, uid, place, _action) in enumerate(rows):
        prev_place = rows[i - 1][2] if i else ""
        prev_uid = rows[i - 1][1][:8] if i else ""
        lines.append(
            f"number={n} uuid={uid[:8]} place≈{place or '—'} "
            f"prev_place≈{prev_place or '—'} prev_uuid={prev_uid or '—'}"
        )
    return "\n".join(lines).strip() + "\n"
