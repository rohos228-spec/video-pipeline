"""«Улучшить сцену»: одна VO-ячейка точечно через 6 нод группы script_frames_qc.

fw_script → fw_check_script → fw_action → fw_shots → fw_qc → fw_report
на одной ячейке доски. Промты — файлы группы + режиссура
``scene_improve_directing_ru.md``: исходные события достраиваются до
монтажной фразы (вход, мост, реакция, следствие), каждому кадру
проставляется покрытие (план, ракурс, движение, стык), паспорт сцены
заполняется и уточняется.
"""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.services.db_apply import extract_apply_ops_json
from app.services.montage_coverage_ops import (
    COVERAGE_ANGLE_CHOICES,
    COVERAGE_LIGHT_CHOICES,
    COVERAGE_MOVE_CHOICES,
    COVERAGE_PLAN_CHOICES,
    COVERAGE_STITCH_CHOICES,
    COVERAGE_VISUAL_TYPE_CHOICES,
    MAX_IMPROVE_SHOTS,
    apply_coverage_scene_action,
    canonical_stitch,
)
from app.services.prompt_library import resolve_script_frames_qc_prompt_path
from app.services.scene_shot_grammar import (
    OBJECTS,
    action_stem,
    camera_pack,
    classify_object,
    fill_bit_spans,
    fill_kadry_scene_numbers,
    visible_len,
)
from app.services.shot_templates import (
    format_scene_chain,
    parse_scene_chain,
    split_scene_action_beats,
)
from app.services.vo_shot_expand import _flag_attrs, bits_from_attrs, main_action_text

GROUP_NODES: tuple[tuple[str, str], ...] = (
    ("fw_script", "Сценарий · биты"),
    ("fw_check_script", "Проверка: сценарий"),
    ("fw_action", "Действие сцены"),
    ("fw_shots", "Сцены → кадры"),
    ("fw_qc", "QC кадров"),
    ("fw_report", "Отчёт"),
)

DIRECTING_PROMPT = "scene_improve_directing_ru"
REPORT_ATTR = "montage_improve_report"

SHOT_ROLES = ("вход", "мост", "действие", "перебивка", "реакция", "следствие")
STITCH_KEYS = tuple(key for key, _label in COVERAGE_STITCH_CHOICES)

_PLAN_ALIASES = {
    "ДЕТАЛЬНЫЙ": "ДЕТАЛЬ",
    "МАКРО": "ДЕТАЛЬ",
    "СРЕДНЕ-КРУПНЫЙ": "КРУПНЫЙ",
    "СРЕДНЕКРУПНЫЙ": "КРУПНЫЙ",
    "ПОЯСНОЙ": "СРЕДНИЙ",
    "ОБЩИЙ ПЛАН": "ОБЩИЙ",
    "ОБЩАЯ": "ОБЩИЙ",
}
_ANGLE_ALIASES = {
    "из-за плеча": "с плеча",
    "ots": "с плеча",
    "фронтально": "фронт",
    "анфас": "фронт",
    "три четверти": "3/4",
    "верхний": "сверху",
    "нижний": "снизу",
}
# Соседние ступени крупности; ОБЩИЙ ↔ ДЕТАЛЬ подряд — прыжок «не через план».
_PLAN_STEP = {"ДАЛЬНИЙ": 0, "ОБЩИЙ": 1, "СРЕДНИЙ": 2, "КРУПНЫЙ": 3, "ДЕТАЛЬ": 4}

_SUB_PLACE_RE = re.compile(r"\s*[:·,]\s*")

AskFn = Callable[[str], Awaitable[str]]


# --------------------------------------------------------------------------- #
# Промты группы
# --------------------------------------------------------------------------- #


def load_group_prompt(name: str) -> str:
    path = resolve_script_frames_qc_prompt_path(name)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def shot_budget(vo: str) -> int:
    """Сколько кадров выдержит закадр ячейки: ~12 знаков на кадр, слово на кадр минимум."""
    text = " ".join((vo or "").split())
    if not text:
        return 1
    words = len(text.split())
    soft = max(3, round(visible_len(text) / 12))
    return max(1, min(MAX_IMPROVE_SHOTS, soft, words))


def _cell_block(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _compose(rules: str, directing: str, task: str, payload: dict[str, Any]) -> str:
    parts = [rules.rstrip()]
    if directing.strip():
        parts.append(directing.rstrip())
    parts.append("## задача оператора\n" + task.strip())
    parts.append("Вход — одна VO-ячейка:\n" + _cell_block(payload))
    return "\n\n".join(parts) + "\n"


def _ops_fields(reply: str, uuid: str) -> dict[str, Any]:
    data = extract_apply_ops_json(reply or "")
    if not data:
        return {}
    ops = data.get("ops") or data.get("actions") or []
    fallback: dict[str, Any] = {}
    for op in ops:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
        if not fields:
            continue
        op_uuid = str(op.get("frame_uuid") or op.get("uuid") or "").strip()
        if uuid and op_uuid == uuid:
            return fields
        if not fallback:
            fallback = fields
    return fallback


# --------------------------------------------------------------------------- #
# fw_script + fw_check_script
# --------------------------------------------------------------------------- #


def check_bits(vo: str, bits: list[Any]) -> str | None:
    """Проверка битов как у fw_check_script: массив, изменение, якоря по порядку."""
    text = " ".join((vo or "").split())
    if not text:
        return None
    rows = [b for b in bits or [] if isinstance(b, dict)]
    if not rows:
        return "нет битов"
    low = text.casefold()
    cursor = 0
    for i, bit in enumerate(sorted(rows, key=lambda b: int(b.get("порядок") or 0)), start=1):
        if not str(bit.get("изменение") or bit.get("глагол") or "").strip():
            return f"бит {i}: пустое изменение"
        anchor = " ".join(str(bit.get("якорь") or "").split()).casefold()
        if not anchor:
            return f"бит {i}: нет якоря"
        idx = low.find(anchor, cursor)
        if idx < 0:
            return f"бит {i}: якорь «{anchor[:40]}» не найден дословно по порядку"
        cursor = idx + len(anchor)
    return None


def build_script_prompt(parent: Frame, vo: str, operator_prompt: str, reason: str) -> str:
    task = (
        "Точечно: биты только этой ячейки закадра. Один op, только `биты`."
    )
    if reason:
        task += f"\nПроверка «сценарий» вернула Не ок: {reason}. Исправь."
    if operator_prompt:
        task += f"\nПромт оператора: {operator_prompt}"
    payload = {
        "frame_uuid": str(parent.uuid or ""),
        "frame_number": int(parent.number),
        "voiceover_text": vo,
    }
    return _compose(load_group_prompt("script_writer_ru"), "", task, payload)


async def stage_script(
    ask: AskFn,
    parent: Frame,
    vo: str,
    operator_prompt: str,
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    bits = bits_from_attrs(parent)
    reason = check_bits(vo, bits)
    if reason is None and bits:
        nodes.append(_node("fw_script", "reused", f"биты ячейки: {len(bits)}"))
        nodes.append(_node("fw_check_script", "ok", "якоря сходятся с закадром"))
        return bits
    tries: list[str] = []
    candidate: list[dict[str, Any]] = []
    for attempt in range(2):
        prompt = build_script_prompt(parent, vo, operator_prompt, reason if attempt else "")
        try:
            reply = await ask(prompt)
        except Exception as exc:  # noqa: BLE001
            tries.append(f"GPT: {type(exc).__name__}: {exc}"[:160])
            break
        raw = _ops_fields(reply, str(parent.uuid or "")).get("биты")
        candidate = fill_bit_spans(vo, raw) if isinstance(raw, list) else []
        reason = check_bits(vo, candidate)
        if reason is None:
            attrs = dict(parent.attrs or {})
            attrs["биты"] = candidate
            parent.attrs = attrs
            _flag_attrs(parent)
            nodes.append(
                _node("fw_script", "ok", f"биты ячейки: {len(candidate)}")
            )
            nodes.append(
                _node(
                    "fw_check_script",
                    "fixed" if attempt else "ok",
                    "Ок" if not attempt else f"Не ок → переписано: {tries[-1] if tries else ''}",
                )
            )
            return candidate
        tries.append(str(reason))
    nodes.append(
        _node(
            "fw_script",
            "warn",
            "биты не собраны — действие по закадру" if not bits else "старые биты",
        )
    )
    nodes.append(_node("fw_check_script", "warn", "; ".join(tries)[:240] or str(reason)))
    return bits or candidate


# --------------------------------------------------------------------------- #
# fw_action
# --------------------------------------------------------------------------- #


def _passport_input(passport: dict[str, Any], character_labels: str) -> dict[str, str]:
    def _get(*keys: str) -> str:
        for key in keys:
            val = str(passport.get(key) or "").strip()
            if val:
                return val
        return ""

    return {
        "смысл": _get("sense", "смысл"),
        "тип": _get("visual_type", "тип"),
        "место": _get("place", "место"),
        "набор": _get("set", "набор"),
        "персонажи": character_labels or _get("characters", "персонажи"),
        "свет": _get("light", "lighting", "освещение"),
        "предметы": _get("props", "предметы"),
        "фон": _get("bg", "фон"),
        "акцент": _get("accent", "акцент"),
        "особенность": _get("feature", "особенность"),
    }


def build_improve_action_prompt(
    *,
    parent: Frame,
    vo: str,
    bits: list[dict[str, Any]],
    passport: dict[str, str],
    operator_prompt: str,
    budget: int,
) -> str:
    source = main_action_text(parent)
    task = (
        "«Улучшить сцену». Возьми исходные события ячейки (биты, текущая цепь, "
        "закадр) и дострой их до монтажной фразы по разделу «Режиссура».\n"
        f"Бюджет: не больше {budget} шагов `→` на всю ячейку.\n"
        "Каждый `→` — отдельный кадр. Все исходные события остаются по порядку; "
        "добавь мосты (вход в место, предмет в руке, второй персонаж до встречи), "
        "реакцию после сильного события и следствие в конце.\n"
        "Весь закадр в скобках без дыр.\n"
        "В `fields` верни `главное_действие` и `паспорт` (объект с полями "
        "смысл, тип, место, набор, персонажи, свет, предметы, фон, акцент, "
        "особенность — по разделу «Паспорт сцены»)."
    )
    if operator_prompt:
        task += f"\nПромт оператора: {operator_prompt}"
    payload = {
        "frame_uuid": str(parent.uuid or ""),
        "frame_number": int(parent.number),
        "voiceover_text": vo,
        "bits": [
            {
                "порядок": b.get("порядок"),
                "якорь": str(b.get("якорь") or "").strip(),
                "изменение": str(b.get("изменение") or b.get("глагол") or "").strip(),
            }
            for b in bits
        ],
        "текущая_цепь": source,
        "паспорт": passport,
        "бюджет_кадров": budget,
        "mode": "improve",
    }
    return _compose(
        load_group_prompt("main_action_from_bits_ru"),
        load_group_prompt(DIRECTING_PROMPT),
        task,
        payload,
    )


def _chain_steps(chain_text: str) -> int:
    return len(split_scene_action_beats(chain_text))


def fallback_chain(parent: Frame, vo: str, operator_prompt: str, place: str) -> str:
    """Цепь без GPT: промт оператора → текущая цепь → закадр одной карточкой."""
    loc = place or "сцена"
    op = (operator_prompt or "").strip()
    if op and len(split_scene_action_beats(op)) >= 2:
        if parse_scene_chain(op):
            return op
        return format_scene_chain([{"n": 1, "place": loc, "action": op, "vo": vo}])
    current = main_action_text(parent)
    if current and parse_scene_chain(current):
        return current
    body = op or "действие сцены"
    return format_scene_chain([{"n": 1, "place": loc, "action": body, "vo": vo}])


async def stage_action(
    ask: AskFn,
    *,
    parent: Frame,
    vo: str,
    bits: list[dict[str, Any]],
    passport: dict[str, str],
    operator_prompt: str,
    budget: int,
    nodes: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    prompt = build_improve_action_prompt(
        parent=parent,
        vo=vo,
        bits=bits,
        passport=passport,
        operator_prompt=operator_prompt,
        budget=budget,
    )
    note = ""
    try:
        reply = await ask(prompt)
    except Exception as exc:  # noqa: BLE001
        reply = ""
        note = f"GPT: {type(exc).__name__}: {exc}"[:160]
    fields = _ops_fields(reply, str(parent.uuid or ""))
    chain = str(fields.get("главное_действие") or fields.get("main_action") or "").strip()
    if not chain and reply:
        parsed = parse_scene_chain(reply)
        if parsed:
            chain = format_scene_chain(parsed)
    gpt_passport = fields.get("паспорт") or fields.get("passport") or {}
    if not isinstance(gpt_passport, dict):
        gpt_passport = {}
    if chain and parse_scene_chain(chain):
        nodes.append(
            _node("fw_action", "ok", f"монтажная фраза: {_chain_steps(chain)} шагов")
        )
        return chain, gpt_passport
    chain = fallback_chain(parent, vo, operator_prompt, passport.get("место") or "")
    nodes.append(
        _node(
            "fw_action",
            "fallback",
            (note or "GPT не вернул цепь") + " — цепь из промта/текущей",
        )
    )
    return chain, gpt_passport


# --------------------------------------------------------------------------- #
# fw_shots — покрытие кадра
# --------------------------------------------------------------------------- #


def canonical_plan(value: Any) -> str:
    raw = " ".join(str(value or "").split()).upper()
    if not raw:
        return ""
    if raw in COVERAGE_PLAN_CHOICES:
        return raw
    if raw in _PLAN_ALIASES:
        return _PLAN_ALIASES[raw]
    for choice in COVERAGE_PLAN_CHOICES:
        if raw.startswith(choice):
            return choice
    return ""


def canonical_angle(value: Any) -> str:
    raw = " ".join(str(value or "").split()).casefold()
    if not raw:
        return ""
    for choice in COVERAGE_ANGLE_CHOICES:
        if raw == choice.casefold():
            return choice
    if raw in _ANGLE_ALIASES:
        return _ANGLE_ALIASES[raw]
    for alias, choice in _ANGLE_ALIASES.items():
        if alias in raw:
            return choice
    for choice in COVERAGE_ANGLE_CHOICES:
        if choice.casefold() in raw:
            return choice
    return ""


def canonical_move(value: Any) -> str:
    raw = " ".join(str(value or "").split()).casefold()
    for choice in COVERAGE_MOVE_CHOICES:
        if raw == choice or raw.startswith(choice):
            return choice
    return ""


def canonical_stitch_key(value: Any) -> str:
    raw = " ".join(str(value or "").split())
    if not raw:
        return ""
    key = canonical_stitch(raw)
    if key in STITCH_KEYS:
        return key
    low = raw.casefold()
    if "жест" in low or "действ" in low or "движен" in low:
        return "cut_on_action"
    if "взгляд" in low:
        return "eyeline"
    if "прям" in low or "склейк" in low:
        return "cut"
    return ""


def _pack_to_ui(pack: dict[str, Any]) -> dict[str, str]:
    plan = canonical_plan(pack.get("план")) or "СРЕДНИЙ"
    point = str(pack.get("точка") or "")
    height = str(pack.get("высота") or "")
    angle = canonical_angle(point) or "фронт"
    if height in {"сверху", "снизу"} and angle == "фронт":
        angle = height
    move = canonical_move(pack.get("движение")) or "статика"
    return {"план": plan, "ракурс": angle, "движение": move}


def _default_role(i: int, n: int, obj: str, place_new: bool) -> str:
    if i == 0 and place_new:
        return "вход"
    if obj == "лицо":
        return "реакция"
    if obj == "предмет":
        return "перебивка" if 0 < i < n - 1 else "действие"
    if i == n - 1 and n > 2:
        return "следствие"
    return "действие"


def _default_stitch(i: int, shot: dict[str, Any], prev: dict[str, Any] | None) -> str:
    if i == 0 or prev is None:
        return "cut"
    if str(prev.get("объект") or "") == "взгляд":
        return "eyeline"
    step = str(shot.get("действие") or "")
    if re.search(r"вход|вош|выход|выш|бег|ид[её]т|откр|закр|доста|полож|бер[её]т|взял", step, re.I):
        return "cut_on_action"
    return "cut"


def _place_root(place: str) -> str:
    return _SUB_PLACE_RE.split(place.strip(), 1)[0].casefold() if place else ""


def normalize_shots(
    shots: list[Any],
    *,
    cell_number: int,
    place: str,
) -> list[dict[str, Any]]:
    """Кадры GPT → enum доски; пустое покрытие дописывает таблица грамматики."""
    rows = [dict(s) for s in shots if isinstance(s, dict) and str(s.get("действие") or s.get("action") or "").strip()]
    out: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    last_root = ""
    n = len(rows)
    for i, row in enumerate(rows):
        step = " ".join(str(row.get("действие") or row.get("action") or "").split())
        shot_place = " ".join(str(row.get("место") or row.get("place") or place or "").split())
        obj = str(row.get("объект") or "").strip().casefold()
        if obj not in OBJECTS:
            obj = classify_object(step)
        root = _place_root(shot_place)
        place_new = i == 0 or (bool(root) and root != last_root)
        pos = "вход" if i == 0 and place_new else ("пик" if i == n - 1 and obj in {"лицо", "предмет", "взгляд"} else "развитие")
        pack = _pack_to_ui(
            camera_pack(
                obj=obj,
                place_new=place_new,
                position=pos,
                hod=bool(re.search(r"ид[её]т|ш[её]л|бежит|бега|вош[её]л|выш[её]л|приш|кра[дс]", step, re.I)),
                prev=None,
            )
        )
        shot = {
            "id": f"{int(cell_number)}-S1-K{i + 1}",
            "parent_id": None if i == 0 else f"{int(cell_number)}-S1-K1",
            "порядок": i + 1,
            "сцена": 1,
            "место": shot_place,
            "действие": step,
            "объект": obj,
            "роль": (
                str(row.get("роль") or "").strip().casefold()
                if str(row.get("роль") or "").strip().casefold() in SHOT_ROLES
                else _default_role(i, n, obj, place_new)
            ),
            "план": canonical_plan(row.get("план")) or pack["план"],
            "ракурс": canonical_angle(row.get("ракурс")) or pack["ракурс"],
            "движение": canonical_move(row.get("движение")) or pack["движение"],
            "закадр": " ".join(str(row.get("закадр") or "").split()),
        }
        shot["стык"] = (
            canonical_stitch_key(row.get("стык") or row.get("переход"))
            or _default_stitch(i, shot, prev)
        )
        if i == 0:
            shot["стык"] = "cut"
        out.append(shot)
        prev = shot
        if root:
            last_root = root
    fill_kadry_scene_numbers(out)
    return out


def shots_from_chain(chain: str, *, cell_number: int, place: str) -> list[dict[str, Any]]:
    """Код fw_shots без GPT: шаги цепи → кадры, камера из таблицы."""
    rows: list[dict[str, Any]] = []
    for scene in parse_scene_chain(chain):
        loc = str(scene.get("place") or "").strip() or place
        for beat in split_scene_action_beats(str(scene.get("action") or "")):
            rows.append({"действие": beat, "место": loc})
    if not rows:
        rows = [{"действие": beat, "место": place} for beat in split_scene_action_beats(chain)]
    return normalize_shots(rows, cell_number=cell_number, place=place)


def build_improve_shots_prompt(
    *,
    parent: Frame,
    vo: str,
    chain: str,
    passport: dict[str, str],
    budget: int,
) -> str:
    task = (
        "«Улучшить сцену»: главное_действие этой ячейки → кадры. "
        "Один шаг `→` = один кадр, порядок тот же.\n"
        "Для каждого кадра дополнительно заполни покрытие по разделу "
        "«Покрытие кадра»: `роль`, `план`, `ракурс`, `движение`, `стык` — "
        "только значения из списков.\n"
        f"Кадров не больше {budget}. Закадр кадра — дословный кусок; склейка = "
        "весь voiceover_text. В этом режиме кусок может быть короче 13 знаков, "
        "но не пустой."
    )
    payload = {
        "frame_uuid": str(parent.uuid or ""),
        "frame_number": int(parent.number),
        "voiceover_text": vo,
        "главное_действие": chain,
        "паспорт": passport,
        "бюджет_кадров": budget,
    }
    return _compose(
        load_group_prompt("scenes_to_frames_ru"),
        load_group_prompt(DIRECTING_PROMPT),
        task,
        payload,
    )


async def stage_shots(
    ask: AskFn,
    *,
    parent: Frame,
    vo: str,
    chain: str,
    passport: dict[str, str],
    budget: int,
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    place = passport.get("место") or ""
    prompt = build_improve_shots_prompt(
        parent=parent, vo=vo, chain=chain, passport=passport, budget=budget
    )
    note = ""
    try:
        reply = await ask(prompt)
    except Exception as exc:  # noqa: BLE001
        reply = ""
        note = f"GPT: {type(exc).__name__}: {exc}"[:160]
    raw = _ops_fields(reply, str(parent.uuid or "")).get("кадры")
    shots = (
        normalize_shots(raw, cell_number=int(parent.number), place=place)
        if isinstance(raw, list)
        else []
    )
    if shots:
        nodes.append(_node("fw_shots", "ok", f"кадров: {len(shots)}, покрытие от GPT"))
        return shots
    shots = shots_from_chain(chain, cell_number=int(parent.number), place=place)
    nodes.append(
        _node(
            "fw_shots",
            "fallback",
            (note or "GPT не вернул кадры") + f" — {len(shots)} кадров по таблице камеры",
        )
    )
    return shots


# --------------------------------------------------------------------------- #
# fw_qc — код чинит, GPT только если брак остался
# --------------------------------------------------------------------------- #


_COGNITIVE_ONLY_RE = re.compile(
    r"^(?:\S+\s+)?(?:понимает|узнаёт|узнает|думает|осознаёт|осознает|вспоминает|знает|решает)\b",
    re.IGNORECASE,
)


def split_vo_for_shots(vo: str, n: int) -> list[str]:
    """Дословные куски закадра на n кадров; при хватке слов — ни одного пустого."""
    from app.services.scene_design.camera_expand import split_text_into_parts

    text = " ".join((vo or "").split())
    if n <= 1:
        return [text]
    parts = [" ".join((p or "").split()) for p in split_text_into_parts(text, n)]
    words = text.split()
    if all(parts) or len(words) < n:
        return parts
    total = sum(len(w) + 1 for w in words)
    out: list[str] = []
    start = 0
    for k in range(n):
        remaining_shots = n - k
        remaining_words = len(words) - start
        if remaining_shots == 1:
            take = remaining_words
        else:
            target = total * (k + 1) / n
            acc = sum(len(w) + 1 for w in words[:start])
            take = 1
            while (
                start + take < len(words)
                and remaining_words - take >= remaining_shots - 1
                and acc + sum(len(w) + 1 for w in words[start : start + take + 1]) <= target
            ):
                take += 1
        out.append(" ".join(words[start : start + take]))
        start += take
    return out


def _glue(shots: list[dict[str, Any]]) -> str:
    return " ".join(
        " ".join(str(s.get("закадр") or "").split()) for s in shots if str(s.get("закадр") or "").strip()
    )


def repair_shots(shots: list[dict[str, Any]], vo: str, cap: int) -> list[str]:
    """Детерминированные правки fw_qc. Возвращает, что починено."""
    fixed: list[str] = []
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    for shot in shots:
        stem = action_stem(str(shot.get("действие") or ""))
        if stem and stem in seen:
            fixed.append(f"убран повтор «{stem[:40]}»")
            continue
        if stem:
            seen.add(stem)
        kept.append(shot)
    if len(kept) > cap:
        fixed.append(f"кадров {len(kept)} > {cap}: хвост мостов сжат")
        kept = _trim_to_cap(kept, cap)
    shots[:] = kept
    text = " ".join((vo or "").split())
    if text:
        empty = any(not str(s.get("закадр") or "").strip() for s in shots)
        if _glue(shots) != text or (empty and len(text.split()) >= len(shots)):
            for shot, piece in zip(shots, split_vo_for_shots(text, len(shots)), strict=False):
                shot["закадр"] = piece
            fixed.append("закадр перераспределён по кадрам дословно")
    prev: dict[str, Any] | None = None
    for shot in shots:
        if prev is not None and shot.get("объект") == prev.get("объект"):
            if shot.get("план") == prev.get("план") and shot.get("ракурс") == prev.get("ракурс"):
                alt = "3/4" if shot.get("ракурс") != "3/4" else "фронт"
                shot["ракурс"] = alt
                fixed.append(f"30°: кадр {shot.get('порядок')} ракурс → {alt}")
        if shot.get("объект") == "предмет" and shot.get("ракурс") == "с плеча":
            shot["ракурс"] = "сверху"
            fixed.append(f"кадр {shot.get('порядок')}: предмет не с плеча → сверху")
        prev = shot
    for i, shot in enumerate(shots, start=1):
        shot["порядок"] = i
        shot["id"] = re.sub(r"K\d+$", f"K{i}", str(shot.get("id") or f"1-S1-K{i}"))
        if i == 1:
            shot["parent_id"] = None
            shot["стык"] = "cut"
    if shots:
        master = str(shots[0].get("id") or "")
        for shot in shots[1:]:
            shot["parent_id"] = master
    return fixed


def _trim_to_cap(shots: list[dict[str, Any]], cap: int) -> list[dict[str, Any]]:
    """Сверх бюджета режем мосты и перебивки, исходные события и следствие держим."""
    out = list(shots)
    for role in ("мост", "перебивка"):
        i = len(out) - 1
        while len(out) > cap and i >= 1:
            if out[i].get("роль") == role:
                out.pop(i)
            i -= 1
    return out[:cap] if len(out) > cap else out


def qc_reasons(shots: list[dict[str, Any]], vo: str) -> tuple[list[str], list[str]]:
    """(брак, замечания) по разделу режиссуры и shots_qc_ru."""
    hard: list[str] = []
    soft: list[str] = []
    if not shots:
        return ["пустые кадры"], soft
    text = " ".join((vo or "").split())
    if text and _glue(shots) != text:
        hard.append("склейка закадр кадров ≠ voiceover_text")
    stems: set[str] = set()
    prev: dict[str, Any] | None = None
    for shot in shots:
        n = shot.get("порядок")
        step = str(shot.get("действие") or "")
        if _COGNITIVE_ONLY_RE.search(step):
            hard.append(f"кадр {n}: вывод вместо видимого действия «{step[:40]}»")
        stem = action_stem(step)
        if stem in stems:
            hard.append(f"кадр {n}: повтор действия «{stem[:40]}»")
        stems.add(stem)
        for key, allowed in (
            ("план", COVERAGE_PLAN_CHOICES),
            ("ракурс", COVERAGE_ANGLE_CHOICES),
            ("движение", COVERAGE_MOVE_CHOICES),
            ("стык", STITCH_KEYS),
        ):
            if shot.get(key) not in allowed:
                hard.append(f"кадр {n}: {key} «{shot.get(key)}» не из списка")
        if text and len(text.split()) >= len(shots) and not str(shot.get("закадр") or "").strip():
            hard.append(f"кадр {n}: пустой закадр")
        if prev is not None:
            a = _PLAN_STEP.get(str(prev.get("план") or ""), -1)
            b = _PLAN_STEP.get(str(shot.get("план") or ""), -1)
            if (
                a >= 0
                and b >= 0
                and abs(a - b) >= 3
                and prev.get("объект") == shot.get("объект")
            ):
                soft.append(
                    f"кадр {n}: {prev.get('план')} → {shot.get('план')} одного объекта — не «через план»"
                )
            if (
                shot.get("объект") == prev.get("объект")
                and shot.get("план") == prev.get("план")
                and shot.get("ракурс") == prev.get("ракурс")
            ):
                hard.append(f"кадр {n}: план и ракурс как у предыдущего (30°)")
        prev = shot
    roles = [str(s.get("роль") or "") for s in shots]
    if len(shots) >= 3 and "реакция" not in roles and not any(s.get("объект") == "лицо" for s in shots):
        soft.append("нет кадра реакции (крупный план лица)")
    if len(shots) >= 2 and shots[0].get("роль") != "вход" and shots[0].get("план") not in {"ОБЩИЙ", "ДАЛЬНИЙ", "ДЕТАЛЬ"}:
        soft.append("первый кадр не вводит место (нет общего/детали входа)")
    return hard, soft


def build_improve_qc_prompt(
    *,
    parent: Frame,
    vo: str,
    shots: list[dict[str, Any]],
    reasons: list[str],
) -> str:
    task = (
        "«Улучшить сцену»: QC кадров этой ячейки. Код нашёл брак:\n- "
        + "\n- ".join(reasons)
        + "\nВерни полный исправленный список `кадры` (с покрытием). "
        "Исходные события и порядок не меняй."
    )
    payload = {
        "frame_uuid": str(parent.uuid or ""),
        "frame_number": int(parent.number),
        "voiceover_text": vo,
        "кадры": shots,
    }
    return _compose(
        load_group_prompt("shots_qc_ru"),
        load_group_prompt(DIRECTING_PROMPT),
        task,
        payload,
    )


async def stage_qc(
    ask: AskFn,
    *,
    parent: Frame,
    vo: str,
    shots: list[dict[str, Any]],
    place: str,
    cap: int,
    nodes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    fixed = repair_shots(shots, vo, cap)
    hard, soft = qc_reasons(shots, vo)
    status = "fixed" if fixed else "ok"
    notes = list(fixed)
    if hard:
        try:
            reply = await ask(
                build_improve_qc_prompt(parent=parent, vo=vo, shots=shots, reasons=hard)
            )
        except Exception as exc:  # noqa: BLE001
            reply = ""
            notes.append(f"GPT QC: {type(exc).__name__}"[:80])
        raw = _ops_fields(reply, str(parent.uuid or "")).get("кадры")
        if isinstance(raw, list) and raw:
            candidate = normalize_shots(raw, cell_number=int(parent.number), place=place)
            notes.extend(repair_shots(candidate, vo, cap))
            c_hard, c_soft = qc_reasons(candidate, vo)
            if len(c_hard) < len(hard):
                shots, hard, soft = candidate, c_hard, c_soft
                notes.append("GPT QC исправил кадры")
                status = "fixed"
        if hard:
            status = "warn"
    nodes.append(
        _node(
            "fw_qc",
            status,
            "; ".join(notes + hard)[:400] or "брака нет",
        )
    )
    return shots, soft


# --------------------------------------------------------------------------- #
# Паспорт → доска
# --------------------------------------------------------------------------- #

_LOCKED_KEYS = ("place", "characters", "set", "light", "visual_type")
_PASSPORT_RU = {
    "sense": "смысл",
    "visual_type": "тип",
    "place": "место",
    "set": "набор",
    "characters": "персонажи",
    "light": "свет",
    "props": "предметы",
    "bg": "фон",
    "accent": "акцент",
    "feature": "особенность",
}


def merge_passport(
    operator: dict[str, Any], gpt: dict[str, Any]
) -> tuple[dict[str, str], list[str]]:
    """Паспорт после улучшения: место/набор/персонажи/свет/тип оператора держим,
    описательные поля — от GPT (или оператора, если GPT пусто)."""
    out: dict[str, str] = {}
    changed: list[str] = []
    for key, ru in _PASSPORT_RU.items():
        mine = str(operator.get(key) or operator.get(ru) or "").strip()
        theirs = " ".join(str(gpt.get(ru) or gpt.get(key) or "").split())
        if key == "light" and theirs and theirs not in COVERAGE_LIGHT_CHOICES:
            theirs = ""
        if key == "visual_type" and theirs and theirs not in COVERAGE_VISUAL_TYPE_CHOICES:
            theirs = ""
        if key in _LOCKED_KEYS and mine:
            value = mine
        else:
            value = theirs or mine
        if value:
            out[key] = value
            if value != mine:
                changed.append(key)
    return out, changed


# --------------------------------------------------------------------------- #
# Пайплайн
# --------------------------------------------------------------------------- #


def _node(key: str, status: str, note: str) -> dict[str, Any]:
    label = dict(GROUP_NODES).get(key, key)
    return {"node": key, "label": label, "status": status, "note": note}


async def _load_frames(session: AsyncSession, project_id: int) -> list[Frame]:
    return list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project_id)
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )


async def improve_cell_scene(
    session: AsyncSession,
    project: Project,
    frame_id: int,
    *,
    operator_prompt: str = "",
    passport: dict[str, Any] | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Одна VO-ячейка через 6 нод группы; кадры вставляются, покрытие и паспорт пишутся."""
    from app.services.gpt_client import gpt_ask_fresh
    from app.services.montage_action_gpt import (
        _map_character_labels,
        apply_cell_passport,
        build_scene_image_ops,
    )
    from app.services.montage_scene_editor import cell_full_text, frame_place, scene_group

    frames = await _load_frames(session, int(project.id))
    frame = next((fr for fr in frames if int(fr.id) == int(frame_id)), None)
    if frame is None:
        raise RuntimeError(f"кадр {frame_id} не найден")
    parent, members = scene_group(frames, frame)
    vo = " ".join(cell_full_text(parent, members).split())
    op_passport = dict(passport or {})
    op_prompt = (operator_prompt or "").strip()
    if not op_passport.get("place") and frame_place(parent):
        op_passport["place"] = frame_place(parent)

    async def ask(text: str) -> str:
        return await gpt_ask_fresh(text, timeout=timeout, project_id=int(project.id))

    labels = await _map_character_labels(
        session,
        int(project.id),
        str(op_passport.get("characters") or "")
        or str((parent.attrs or {}).get("персонажи_сцены") or ""),
    )
    pass_in = _passport_input(op_passport, labels)
    budget = shot_budget(vo)
    nodes: list[dict[str, Any]] = []

    bits = await stage_script(ask, parent, vo, op_prompt, nodes)
    chain, gpt_passport = await stage_action(
        ask,
        parent=parent,
        vo=vo,
        bits=bits,
        passport=pass_in,
        operator_prompt=op_prompt,
        budget=budget,
        nodes=nodes,
    )
    new_passport, changed = merge_passport(op_passport, gpt_passport)
    pass_after = _passport_input(new_passport, labels)
    shots = await stage_shots(
        ask,
        parent=parent,
        vo=vo,
        chain=chain,
        passport=pass_after,
        budget=budget,
        nodes=nodes,
    )
    shots, warnings = await stage_qc(
        ask,
        parent=parent,
        vo=vo,
        shots=shots,
        place=pass_after.get("место") or "",
        cap=min(MAX_IMPROVE_SHOTS, max(budget, 1)),
        nodes=nodes,
    )
    if not shots:
        raise RuntimeError("улучшение не дало ни одного кадра")

    applied = apply_cell_passport(parent, frames, new_passport)
    await session.flush()
    chain_text = format_scene_chain(
        [
            {
                "n": i,
                "place": str(s.get("место") or ""),
                "action": str(s.get("действие") or ""),
                "vo": str(s.get("закадр") or ""),
            }
            for i, s in enumerate(shots, start=1)
        ]
    )
    frames = await _load_frames(session, int(project.id))
    parent, _members = scene_group(frames, parent)
    apply_report = await apply_coverage_scene_action(
        session,
        project,
        parent,
        frames,
        chain_text,
        grow=True,
        kadry=shots,
    )
    await session.flush()

    report = {
        "nodes": nodes,
        "budget": budget,
        "shots": [
            {
                k: s.get(k)
                for k in ("порядок", "роль", "действие", "объект", "план", "ракурс", "движение", "стык", "закадр")
            }
            for s in shots
        ],
        "passport": new_passport,
        "passport_changed": changed,
        "warnings": warnings,
    }
    nodes.append(
        _node(
            "fw_report",
            "ok",
            f"кадров {len(shots)} · +{apply_report.get('inserted_frames') or 0} новых"
            + (f" · паспорт: {', '.join(_PASSPORT_RU[k] for k in changed)}" if changed else "")
            + (f" · замечаний {len(warnings)}" if warnings else ""),
        )
    )
    frames = await _load_frames(session, int(project.id))
    parent, members = scene_group(frames, parent)
    attrs = dict(parent.attrs or {})
    attrs[REPORT_ATTR] = report
    parent.attrs = attrs
    _flag_attrs(parent)
    await session.flush()

    ops = build_scene_image_ops(
        members,
        passport=new_passport,
        chain=apply_report.get("chain") or chain_text,
        frame_ids=None,
        kadry=shots,
    )
    logger.info(
        "montage improve #{} cell={} shots={} inserted={} passport={} nodes={}",
        project.id,
        parent.number,
        len(shots),
        apply_report.get("inserted_frames"),
        applied,
        [(n["node"], n["status"]) for n in nodes],
    )
    return {
        "ok": True,
        "mode": "improve",
        "frame_id": int(parent.id),
        "frame_number": int(parent.number),
        "replace_ns": [],
        "passport_applied": applied,
        "chain": apply_report.get("chain") or chain_text,
        "shots": apply_report.get("shots"),
        "skipped_shots": apply_report.get("skipped_shots"),
        "inserted_frames": apply_report.get("inserted_frames"),
        "report": apply_report,
        "improve_report": report,
        "image_ops": ops,
        "images": len(ops),
    }
