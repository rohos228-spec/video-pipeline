"""Шаблоны сцена → кадры (T0–T10, X1/X2) для ноды script_frames_qc / shots."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.project_root import find_project_root

_SCENE_SPLIT = re.compile(r"\n(?=\d+\.\s)")
_SCENE_HEAD = re.compile(r"^(\d+)\.\s*(.+)$")


def _has(pat: str, text: str) -> bool:
    return bool(re.search(pat, text, flags=re.IGNORECASE))

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


def parse_scene_chain(action: str) -> list[dict[str, Any]]:
    """Сцены из главное_действие: N. место — действие + (закадр)."""
    text = (action or "").strip()
    if not text:
        return []
    scenes: list[dict[str, Any]] = []
    for raw in _SCENE_SPLIT.split(text):
        raw = raw.strip()
        if not raw:
            continue
        lines = raw.split("\n", 1)
        head = lines[0].strip()
        vo = lines[1].strip() if len(lines) > 1 else ""
        m = _SCENE_HEAD.match(head)
        if not m:
            continue
        rest = m.group(2).strip()
        if " — " in rest:
            place, act = rest.split(" — ", 1)
        elif " - " in rest:
            place, act = rest.split(" - ", 1)
        else:
            place, act = "", rest
        scenes.append(
            {
                "n": int(m.group(1)),
                "place": place.strip(),
                "action": act.strip(),
                "vo": vo,
                "blob": f"{place} {act} {vo}",
            }
        )
    return scenes


def select_template_when(scene: dict[str, Any], prev_place: str = "") -> str:
    """Первое «да» по when / Выбор. X1 если место не сменилось."""
    blob = str(scene.get("blob") or "")
    place = str(scene.get("place") or "").strip().casefold()
    prev = (prev_place or "").strip().casefold()
    same = bool(place and prev and place == prev)
    if _has(r"титр|имя появляется", blob) and not same:
        return "T0"
    if _has(r"говор(ят|ит)|спор(ят|ит)|спрашив", blob):
        return "T1"
    if _has(r"нашёл|нашел|взял|прочитал|достал", blob) and (
        (not same) or _has(r"папк|документ|книг|схем", blob)
    ):
        return "T2"
    if same:
        if _has(r"смотр|наблюд|взгляд|видит", blob):
            return "T4"
        if _has(
            r"листа|пишет|моет|режет|счита|отпечат|схем|отмеча|"
            r"оформл|расклад|упаков|доказател",
            blob,
        ):
            return "T5"
        if _has(r"уход|покид|пустое место|закрывает", blob):
            return "T10"
        if _has(r"услышал|понял|получил|вопрос на доске|появляется вопрос", blob):
            return "T7"
        return "X1"
    if _has(r"родил|служб[аеуы]|арми|1980|а потом|друг(ая|ой) жизн", blob):
        return "T8"
    if _has(r"идёт|едет|вышел из|приехал|поезд", blob) and _has(
        r"из |в |к |от |до ", blob
    ):
        return "T6"
    if place:
        return "T3"
    if _has(r"уход|покид|пустое", blob):
        return "T10"
    return "T0"


def normalize_template_id(tid: str) -> str:
    raw = (tid or "").strip().upper()
    match = re.match(r"^([TX]\d+)", raw)
    return match.group(1) if match else raw


def template_max_shots(tid: str) -> int:
    """Сколько строк shots у шаблона. 0 если id нет в каталоге."""
    base = normalize_template_id(tid)
    if not base:
        return 0
    rows = [
        row
        for row in (load_shot_templates().get("shots") or [])
        if normalize_template_id(str(row.get("template_id") or "")) == base
    ]
    return len(rows)


_ALT_SAME = {
    "T5": "T4",
    "T4": "T7",
    "T7": "T9",
    "T9": "X1",
    "T2": "T5",
    "T3": "X1",
    "T10": "X1",
    "T1": "T4",
    "T6": "T10",
    "T8": "X1",
    "X1": "T4",
    "X2": "T3",
}
_ALT_NEW = {
    "T3": "T8",
    "T8": "T3",
    "T2": "T3",
    "T6": "T3",
    "T0": "T3",
    "T5": "T3",
    "T4": "T3",
}


def avoid_repeat_template(tid: str, prev: str, *, same_place: bool) -> str:
    """Соседние сцены не могут нести один T*, кроме T0."""
    cur = normalize_template_id(tid)
    last = normalize_template_id(prev)
    if not last or cur != last or cur == "T0":
        return cur or tid
    alt = (_ALT_SAME if same_place else _ALT_NEW).get(cur) or ("X1" if same_place else "T3")
    if alt == last:
        alt = "T0"
    return alt


def assign_templates_for_action(action: str) -> dict[int, str]:
    assigned: dict[int, str] = {}
    prev_place = ""
    prev_tid = ""
    for scene in parse_scene_chain(action):
        tid = select_template_when(scene, prev_place)
        place = str(scene.get("place") or "").strip()
        same = bool(place and prev_place and place.casefold() == prev_place.casefold())
        tid = avoid_repeat_template(tid, prev_tid, same_place=same)
        assigned[int(scene["n"])] = tid
        if place:
            prev_place = place
        prev_tid = tid
    return assigned


def coverage_template_reason(shots: list[dict[str, Any]], uid: str = "") -> str | None:
    """Один T на сцену, без удлинения и без одинаковых T подряд (кроме T0)."""
    prefix = f"uuid {uid[:8]}: " if uid else ""
    blocks: list[tuple[str, str, int]] = []
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        sc = shot.get("сцена")
        place = str(shot.get("место") or shot.get("place") or "").strip().casefold()
        key = str(sc) if sc not in (None, "") else f"p:{place}"
        tid = normalize_template_id(str(shot.get("шаблон") or shot.get("template") or ""))
        if not blocks or blocks[-1][0] != key:
            blocks.append((key, tid, 1))
            continue
        prev_key, prev_tid, n = blocks[-1]
        if tid and prev_tid and tid != prev_tid:
            return f"{prefix}сцена {key}: два шаблона {prev_tid} и {tid}"
        blocks[-1] = (prev_key, prev_tid or tid, n + 1)
    prev_tid = ""
    for key, tid, n in blocks:
        if tid and prev_tid and tid == prev_tid and tid != "T0":
            return f"{prefix}шаблон {tid} подряд на соседних сценах — смени T*"
        if tid:
            limit = template_max_shots(tid)
            if limit and n > limit:
                return (
                    f"{prefix}сцена {key}: {tid} удлинили до {n} кадров "
                    f"(в таблице {limit})"
                )
        if tid:
            prev_tid = tid
    return None


def format_when_assignments(frames: list[Any]) -> str:
    """Готовый select по when — GPT не выбирает шаблон сам."""
    lines = [
        "# SELECT ПО when (обязательно, id не менять)",
        "Столбец when из таблицы шаблонов. Первое подходящее → T*/X*.",
        "То же место, что у предыдущей сцены → X1, если нет более точного when.",
    ]
    any_row = False
    for fr in frames:
        attrs = getattr(fr, "attrs", None)
        if attrs is None and isinstance(fr, dict):
            attrs = fr.get("attrs") or fr
        if not isinstance(attrs, dict):
            continue
        action = str(
            attrs.get("главное_действие") or attrs.get("main_action") or ""
        ).strip()
        if not action:
            continue
        assigned = assign_templates_for_action(action)
        by_id = {
            str(t.get("id")): t
            for t in (load_shot_templates().get("templates") or [])
        }
        for scene in parse_scene_chain(action):
            tid = assigned.get(int(scene["n"])) or select_template_when(scene, "")
            when = (by_id.get(tid) or {}).get("when") or ""
            lines.append(
                f"сцена {scene['n']} | {scene['place'] or '—'} | "
                f"{scene['action'][:80]} | when={when} → {tid}"
            )
            any_row = True
    if not any_row:
        return ""
    return "\n".join(lines).strip() + "\n"


def format_shot_templates_catalog(*, max_chars: int = 12000) -> str:
    """Компактный каталог для accompanying ноды shots."""
    data = load_shot_templates()
    lines: list[str] = [
        "# ШАБЛОНЫ СЦЕНА→КАДРЫ (SoT)",
        "T = тип сцены. X = стык со следующей ячейкой.",
        "K = кадр внутри типа. required=1 нельзя выкинуть.",
        "drop_order = порядок удаления при коротком закадре.",
        "axis: нет | keep_sides | look_chain — см. rules ниже.",
        "select — на КАЖДУЮ сцену главное_действие, не на ячейку целиком.",
        "T8 = один ОБЩИЙ на один новый мир/жизнь. То же место после T8 → X1/T5/T9.",
        "Одно место: меняй план и ракурс по лестнице. Нельзя все ОБЩИЙ/фронт.",
        "ЗАПРЕЩЕНО: удлинять T* сверх строк shots. T5 = макс 4, T3 = макс 2, T0 = 1.",
        "ЗАПРЕЩЕНО: один и тот же T* на соседних сценах (кроме T0). Бери X1 / другой T.",
        "У каждого кадра свой дословный закадр. Пустой закадр запрещён.",
        "Кадров больше, чем клауз — удаляй по drop_order, не оставляй пустой закадр.",
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
