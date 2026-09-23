"""«Улучшить сцену»: одна VO-ячейка точечно через 6 нод группы script_frames_qc.

fw_script → fw_check_script → fw_action → fw_shots → fw_qc → fw_report
на одной ячейке доски. Промты — файлы группы + режиссура
``scene_improve_directing_ru.md``: исходные события достраиваются до
монтажной фразы (вход, мост, реакция, следствие), каждому кадру
проставляется покрытие (план, ракурс, движение, стык), паспорт сцены
заполняется и уточняется.

Якоря сцены — граница текста. Каждый якорь владеет своим куском закадра
(нарезка как на доске, ``split_vo_by_anchors``); кадры якоря берут закадр
только из его куска. Якоря оператора GPT не переписывает и не добавляет.
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
from app.services.montage_scene_editor import (
    anchor_positions,
    normalize_anchor_rows,
    split_vo_by_anchors,
)
from app.services.prompt_library import resolve_script_frames_qc_prompt_path
from app.services.scene_shot_grammar import (
    OBJECTS,
    action_stem,
    camera_pack,
    classify_object,
    fill_bit_spans,
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

_SUB_PLACE_RE = re.compile(r"\s*[:·]\s*")

AskFn = Callable[[str], Awaitable[str]]


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split())


# --------------------------------------------------------------------------- #
# Промты группы
# --------------------------------------------------------------------------- #


def load_group_prompt(name: str) -> str:
    path = resolve_script_frames_qc_prompt_path(name)
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def shot_budget(vo: str) -> int:
    """Сколько кадров выдержит кусок закадра: ~12 знаков на кадр, слово на кадр минимум."""
    text = _norm(vo)
    if not text:
        return 1
    words = len(text.split())
    soft = max(3, round(visible_len(text) / 12))
    return max(1, min(MAX_IMPROVE_SHOTS, soft, words))


def allot_budgets(pieces: list[str]) -> list[int]:
    """Бюджет кадров по якорям: сумма ≤ max(12, число якорей), минимум 1 на якорь."""
    raw = [shot_budget(p) for p in pieces]
    cap = max(MAX_IMPROVE_SHOTS, len(pieces))
    total = sum(raw)
    if total <= cap:
        return raw
    out = [max(1, int(b * cap / total)) for b in raw]
    while sum(out) > cap:
        i = max(range(len(out)), key=lambda k: out[k])
        if out[i] <= 1:
            break
        out[i] -= 1
    return out


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


def _units_payload(units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "n": u["n"],
            "якорь": u["якорь"],
            "изменение": u["изменение"],
            "закадр": u["закадр"],
            "бюджет_кадров": u["бюджет"],
        }
        for u in units
    ]


# --------------------------------------------------------------------------- #
# fw_script + fw_check_script — якоря = границы текста
# --------------------------------------------------------------------------- #


def check_bits(vo: str, bits: list[Any]) -> str | None:
    """Проверка битов как у fw_check_script: массив, изменение, якоря по порядку."""
    text = _norm(vo)
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
        anchor = _norm(bit.get("якорь")).casefold()
        if not anchor:
            return f"бит {i}: нет якоря"
        idx = low.find(anchor, cursor)
        if idx < 0:
            return f"бит {i}: якорь «{anchor[:40]}» не найден дословно по порядку"
        cursor = idx + len(anchor)
    return None


def anchor_units(vo: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Якоря → куски закадра, как их режет доска. Ненайденный якорь — ошибка."""
    text = _norm(vo)
    ordered = sorted(
        (r for r in rows if _norm(r.get("якорь"))),
        key=lambda r: int(r.get("порядок") or 0),
    )
    texts = [_norm(r.get("якорь")) for r in ordered]
    lost = [texts[i] for i, pos in enumerate(anchor_positions(text, texts)) if pos < 0]
    if lost:
        raise RuntimeError(
            "якорь не найден по порядку в закадре сцены: "
            + "; ".join(f"«{x}»" for x in lost)
            + " — поправьте якоря: текст других якорей в сцену не берём"
        )
    pieces = split_vo_by_anchors(text, texts)
    if len(pieces) != len(texts):
        raise RuntimeError(
            f"якорей {len(texts)}, а кусков закадра {len(pieces)}: "
            "два якоря стоят в одном месте текста"
        )
    budgets = allot_budgets(pieces)
    return [
        {
            "n": i + 1,
            "якорь": texts[i],
            "изменение": _norm(row.get("изменение") or row.get("глагол")),
            "закадр": pieces[i],
            "бюджет": budgets[i],
        }
        for i, row in enumerate(ordered)
    ]


def build_script_prompt(parent: Frame, vo: str, operator_prompt: str, reason: str) -> str:
    task = "Точечно: биты только этой ячейки закадра. Один op, только `биты`."
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


def _save_bits(parent: Frame, rows: list[dict[str, Any]]) -> None:
    attrs = dict(parent.attrs or {})
    attrs["биты"] = rows
    parent.attrs = attrs
    _flag_attrs(parent)


async def stage_script(
    ask: AskFn,
    parent: Frame,
    vo: str,
    operator_prompt: str,
    nodes: list[dict[str, Any]],
    *,
    anchors: list[Any] | None = None,
) -> list[dict[str, Any]]:
    """Куски сцены по якорям. Якоря есть — GPT биты не пишет."""
    given = normalize_anchor_rows(anchors) if anchors else []
    own = [b for b in bits_from_attrs(parent) if _norm(b.get("якорь"))]
    rows = given or own
    if rows:
        units = anchor_units(vo, rows)
        if given:
            merged = [
                {**row, "закадр": units[i]["закадр"]} for i, row in enumerate(given)
            ]
            _save_bits(parent, merged)
        nodes.append(
            _node(
                "fw_script",
                "reused",
                f"якоря сцены: {len(units)} — GPT их не меняет",
            )
        )
        nodes.append(
            _node(
                "fw_check_script",
                "ok",
                "каждый якорь найден; текст сцены = куски этих якорей",
            )
        )
        return units

    reason = "нет битов"
    tries: list[str] = []
    for attempt in range(2):
        prompt = build_script_prompt(parent, vo, operator_prompt, reason if attempt else "")
        try:
            reply = await ask(prompt)
        except Exception as exc:  # noqa: BLE001
            tries.append(f"GPT: {type(exc).__name__}: {exc}"[:160])
            break
        raw = _ops_fields(reply, str(parent.uuid or "")).get("биты")
        candidate = fill_bit_spans(vo, raw) if isinstance(raw, list) else []
        reason = check_bits(vo, candidate) or ""
        if not reason:
            try:
                units = anchor_units(vo, candidate)
            except RuntimeError as exc:
                reason = str(exc)
            else:
                _save_bits(parent, candidate)
                nodes.append(_node("fw_script", "ok", f"биты ячейки: {len(units)}"))
                nodes.append(
                    _node(
                        "fw_check_script",
                        "fixed" if attempt else "ok",
                        f"Не ок → переписано: {tries[-1]}" if attempt and tries else "Ок",
                    )
                )
                return units
        tries.append(reason)
    nodes.append(_node("fw_script", "warn", "биты не собраны — весь закадр одним куском"))
    nodes.append(_node("fw_check_script", "warn", "; ".join(tries)[:240]))
    text = _norm(vo)
    return [
        {
            "n": 1,
            "якорь": " ".join(text.split()[:8]),
            "изменение": "",
            "закадр": text,
            "бюджет": allot_budgets([text])[0],
        }
    ]


# --------------------------------------------------------------------------- #
# fw_action — карточка N. на каждый якорь
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
    units: list[dict[str, Any]],
    passport: dict[str, str],
    operator_prompt: str,
) -> str:
    k = len(units)
    task = (
        "«Улучшить сцену». Дострой исходные события ячейки до монтажной фразы "
        "по разделу «Режиссура».\n"
        f"Якоря сцены заданы: их ровно {k}. Верни ровно {k} карточек `N.` — "
        "N = номер якоря, по порядку. В скобках карточки — дословно `закадр` "
        "этого якоря, ни словом больше или меньше. Шаги карточки показывают "
        "только то, о чём закадр этого якоря; не больше `бюджет_кадров` шагов "
        "на карточку. Якоря не добавляй, не убирай, не объединяй.\n"
        "Каждый `→` — отдельный кадр. Все исходные события остаются по порядку; "
        "добавь мосты (вход в место, предмет в руке, второй персонаж до встречи), "
        "реакцию после сильного события и следствие в конце.\n"
        "Место карточки — `место` паспорта.\n"
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
        "якоря": _units_payload(units),
        "текущая_цепь": main_action_text(parent),
        "паспорт": passport,
        "mode": "improve",
    }
    return _compose(
        load_group_prompt("main_action_from_bits_ru"),
        load_group_prompt(DIRECTING_PROMPT),
        task,
        payload,
    )


def _card_steps(card: dict[str, Any]) -> list[str]:
    return split_scene_action_beats(_norm(card.get("action")))


def fit_cards_to_units(
    cards: list[dict[str, Any]], units: list[dict[str, Any]], place: str
) -> list[dict[str, Any]] | None:
    """Карточки GPT → по одной на якорь с его куском закадра. None — не сходится."""
    if not cards:
        return None
    if len(cards) != len(units):
        if len(units) != 1:
            return None
        steps = [s for c in cards for s in _card_steps(c)]
        cards = [{"n": 1, "place": cards[0].get("place"), "action": " → ".join(steps)}]
    out: list[dict[str, Any]] = []
    for card, unit in zip(cards, units, strict=True):
        steps = _card_steps(card)
        if not steps:
            return None
        out.append(
            {
                "n": unit["n"],
                "place": place or _norm(card.get("place")),
                "action": " → ".join(steps),
                "vo": unit["закадр"],
            }
        )
    return out


def fallback_cards(
    parent: Frame, units: list[dict[str, Any]], operator_prompt: str, place: str
) -> list[dict[str, Any]]:
    """Без GPT: промт оператора (одна сцена) → текущая цепь → изменение якоря."""
    op = _norm(operator_prompt)
    if len(units) == 1 and op and len(split_scene_action_beats(op)) >= 2:
        parsed = parse_scene_chain(op)
        cards = parsed or [{"n": 1, "place": place, "action": op}]
        fitted = fit_cards_to_units(cards, units, place)
        if fitted:
            return fitted
    fitted = fit_cards_to_units(parse_scene_chain(main_action_text(parent)), units, place)
    if fitted:
        return fitted
    return [
        {
            "n": u["n"],
            "place": place,
            "action": u["изменение"] or (op if len(units) == 1 and op else "действие сцены"),
            "vo": u["закадр"],
        }
        for u in units
    ]


async def stage_action(
    ask: AskFn,
    *,
    parent: Frame,
    vo: str,
    units: list[dict[str, Any]],
    passport: dict[str, str],
    operator_prompt: str,
    nodes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    place = passport.get("место") or ""
    prompt = build_improve_action_prompt(
        parent=parent,
        vo=vo,
        units=units,
        passport=passport,
        operator_prompt=operator_prompt,
    )
    note = ""
    try:
        reply = await ask(prompt)
    except Exception as exc:  # noqa: BLE001
        reply = ""
        note = f"GPT: {type(exc).__name__}: {exc}"[:160]
    fields = _ops_fields(reply, str(parent.uuid or ""))
    chain = str(fields.get("главное_действие") or fields.get("main_action") or "").strip()
    cards_raw = parse_scene_chain(chain) or (parse_scene_chain(reply) if reply else [])
    gpt_passport = fields.get("паспорт") or fields.get("passport") or {}
    if not isinstance(gpt_passport, dict):
        gpt_passport = {}
    cards = fit_cards_to_units(cards_raw, units, place)
    if cards:
        steps = sum(len(_card_steps(c)) for c in cards)
        nodes.append(
            _node(
                "fw_action",
                "ok",
                f"монтажная фраза: {steps} шагов на {len(cards)} якор(ь/я/ей)",
            )
        )
        return cards, gpt_passport
    if cards_raw and not note:
        note = f"GPT вернул {len(cards_raw)} карточек на {len(units)} якорей"
    cards = fallback_cards(parent, units, operator_prompt, place)
    nodes.append(
        _node(
            "fw_action",
            "fallback",
            (note or "GPT не вернул цепь") + " — цепь из промта/текущей",
        )
    )
    return cards, gpt_passport


# --------------------------------------------------------------------------- #
# fw_shots — покрытие кадра
# --------------------------------------------------------------------------- #


def canonical_plan(value: Any) -> str:
    raw = _norm(value).upper()
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
    raw = _norm(value).casefold()
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
    raw = _norm(value).casefold()
    for choice in COVERAGE_MOVE_CHOICES:
        if raw == choice or raw.startswith(choice):
            return choice
    return ""


def canonical_stitch_key(value: Any) -> str:
    raw = _norm(value)
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


def _split_place(raw: str, place: str) -> tuple[str, str]:
    """(место сцены, зона). Место паспорта не меняется; «дом: коридор» → зона."""
    text = _norm(raw)
    if not place:
        return text, ""
    if not text or text.casefold() == place.casefold():
        return place, ""
    head, _sep, tail = text.partition(":") if ":" in text else text.partition("·")
    if tail and head.strip().casefold() == place.casefold():
        return place, _norm(tail)
    if text.casefold().startswith(place.casefold()):
        return place, _norm(text[len(place) :].strip(" ,:·—-"))
    return place, text


def _anchor_n(row: dict[str, Any], k: int) -> int | None:
    for key in ("якорь_n", "якорь", "n_якоря"):
        raw = row.get(key)
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= k:
            return n
    return None


def normalize_shots(
    shots: list[Any],
    *,
    cell_number: int,
    place: str,
    anchors: int = 1,
) -> list[dict[str, Any]]:
    """Кадры GPT → enum доски; пустое покрытие дописывает таблица грамматики."""
    rows = [
        dict(s)
        for s in shots
        if isinstance(s, dict) and _norm(s.get("действие") or s.get("action"))
    ]
    out: list[dict[str, Any]] = []
    prev: dict[str, Any] | None = None
    n = len(rows)
    for i, row in enumerate(rows):
        step = _norm(row.get("действие") or row.get("action"))
        shot_place, zone = _split_place(_norm(row.get("место") or row.get("place")), place)
        obj = _norm(row.get("объект")).casefold()
        if obj not in OBJECTS:
            obj = classify_object(step)
        place_new = i == 0
        pos = (
            "вход"
            if place_new
            else ("пик" if i == n - 1 and obj in {"лицо", "предмет", "взгляд"} else "развитие")
        )
        pack = _pack_to_ui(
            camera_pack(
                obj=obj,
                place_new=place_new,
                position=pos,
                hod=bool(re.search(r"ид[её]т|ш[её]л|бежит|бега|вош[её]л|выш[её]л|приш|кра[дс]", step, re.I)),
                prev=None,
            )
        )
        role = _norm(row.get("роль")).casefold()
        shot = {
            "id": f"{int(cell_number)}-S1-K{i + 1}",
            "parent_id": None if i == 0 else f"{int(cell_number)}-S1-K1",
            "порядок": i + 1,
            "сцена": 1,
            "якорь_n": _anchor_n(row, anchors) if anchors > 1 else 1,
            "место": shot_place,
            "зона": _norm(row.get("зона")) or zone,
            "действие": step,
            "объект": obj,
            "роль": role if role in SHOT_ROLES else _default_role(i, n, obj, place_new),
            "план": canonical_plan(row.get("план")) or pack["план"],
            "ракурс": canonical_angle(row.get("ракурс")) or pack["ракурс"],
            "движение": canonical_move(row.get("движение")) or pack["движение"],
            "закадр": _norm(row.get("закадр")),
        }
        shot["стык"] = (
            canonical_stitch_key(row.get("стык") or row.get("переход"))
            or _default_stitch(i, shot, prev)
        )
        if i == 0:
            shot["стык"] = "cut"
        out.append(shot)
        prev = shot
    return out


def anchors_grouped(shots: list[dict[str, Any]], k: int) -> bool:
    """Кадры идут по якорям подряд 1..k, у каждого якоря есть кадр."""
    seq = [s.get("якорь_n") for s in shots]
    if any(not isinstance(v, int) for v in seq):
        return False
    return seq == sorted(seq) and set(seq) == set(range(1, k + 1))


def shots_from_cards(
    cards: list[dict[str, Any]], *, cell_number: int, place: str
) -> list[dict[str, Any]]:
    """Код fw_shots без GPT: шаги карточек → кадры, камера из таблицы."""
    rows = [
        {"действие": step, "место": card.get("place") or place, "якорь_n": card["n"]}
        for card in cards
        for step in _card_steps(card)
    ]
    return normalize_shots(rows, cell_number=cell_number, place=place, anchors=len(cards))


def build_improve_shots_prompt(
    *,
    parent: Frame,
    vo: str,
    cards: list[dict[str, Any]],
    units: list[dict[str, Any]],
    passport: dict[str, str],
) -> str:
    task = (
        "«Улучшить сцену»: главное_действие этой ячейки → кадры. "
        "Один шаг `→` = один кадр, порядок тот же.\n"
        "Для каждого кадра дополнительно заполни покрытие по разделу "
        "«Покрытие кадра»: `роль`, `план`, `ракурс`, `движение`, `стык` — "
        "только значения из списков.\n"
        "`якорь_n` каждого кадра = N карточки, из которой шаг. Закадр кадра — "
        "дословный кусок `закадр` **этого** якоря; склейка закадра кадров "
        "одного якоря = его `закадр`. Текст другого якоря в кадр не бери. "
        "Кусок может быть короче 13 знаков, но не пустой.\n"
        "`место` — место паспорта; часть места («коридор») пиши в `зона`."
    )
    payload = {
        "frame_uuid": str(parent.uuid or ""),
        "frame_number": int(parent.number),
        "voiceover_text": vo,
        "якоря": _units_payload(units),
        "главное_действие": format_scene_chain(cards),
        "паспорт": passport,
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
    cards: list[dict[str, Any]],
    units: list[dict[str, Any]],
    passport: dict[str, str],
    nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    place = passport.get("место") or ""
    k = len(units)
    prompt = build_improve_shots_prompt(
        parent=parent, vo=vo, cards=cards, units=units, passport=passport
    )
    note = ""
    try:
        reply = await ask(prompt)
    except Exception as exc:  # noqa: BLE001
        reply = ""
        note = f"GPT: {type(exc).__name__}: {exc}"[:160]
    raw = _ops_fields(reply, str(parent.uuid or "")).get("кадры")
    shots = (
        normalize_shots(raw, cell_number=int(parent.number), place=place, anchors=k)
        if isinstance(raw, list)
        else []
    )
    canonical = shots_from_cards(cards, cell_number=int(parent.number), place=place)
    if shots and not anchors_grouped(shots, k) and len(shots) == len(canonical):
        for shot, ref in zip(shots, canonical, strict=True):
            shot["якорь_n"] = ref["якорь_n"]
    if shots and anchors_grouped(shots, k):
        nodes.append(_node("fw_shots", "ok", f"кадров: {len(shots)}, покрытие от GPT"))
        return shots
    if shots and not note:
        note = "кадры GPT не разложены по якорям"
    nodes.append(
        _node(
            "fw_shots",
            "fallback",
            (note or "GPT не вернул кадры") + f" — {len(canonical)} кадров по таблице камеры",
        )
    )
    return canonical


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

    text = _norm(vo)
    if n <= 1:
        return [text]
    parts = [_norm(p) for p in split_text_into_parts(text, n)]
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
    return " ".join(_norm(s.get("закадр")) for s in shots if _norm(s.get("закадр")))


def _group(shots: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    return [s for s in shots if s.get("якорь_n") == n]


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


def repair_shots(shots: list[dict[str, Any]], units: list[dict[str, Any]]) -> list[str]:
    """Детерминированные правки fw_qc по якорям. Возвращает, что починено."""
    fixed: list[str] = []
    seen: set[str] = set()
    kept: list[dict[str, Any]] = []
    for shot in shots:
        stem = action_stem(str(shot.get("действие") or ""))
        alone = len(_group(shots, shot.get("якорь_n"))) <= 1
        if stem and stem in seen and not alone:
            fixed.append(f"убран повтор «{stem[:40]}»")
            continue
        if stem:
            seen.add(stem)
        kept.append(shot)
    rebuilt: list[dict[str, Any]] = []
    for unit in units:
        group = _group(kept, unit["n"])
        if len(group) > unit["бюджет"]:
            fixed.append(f"якорь {unit['n']}: кадров {len(group)} > {unit['бюджет']} — мосты сжаты")
            group = _trim_to_cap(group, unit["бюджет"])
        piece = _norm(unit["закадр"])
        empty = any(not _norm(s.get("закадр")) for s in group)
        if group and (_glue(group) != piece or (empty and len(piece.split()) >= len(group))):
            for shot, part in zip(group, split_vo_for_shots(piece, len(group)), strict=False):
                shot["закадр"] = part
            fixed.append(f"якорь {unit['n']}: закадр кадров = кусок якоря дословно")
        rebuilt.extend(group)
    shots[:] = rebuilt
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


def qc_reasons(
    shots: list[dict[str, Any]], units: list[dict[str, Any]]
) -> tuple[list[str], list[str]]:
    """(брак, замечания) по разделу режиссуры и shots_qc_ru."""
    hard: list[str] = []
    soft: list[str] = []
    if not shots:
        return ["пустые кадры"], soft
    if not anchors_grouped(shots, len(units)):
        hard.append("кадры не разложены по якорям подряд")
    for unit in units:
        group = _group(shots, unit["n"])
        if group and _glue(group) != _norm(unit["закадр"]):
            hard.append(f"якорь {unit['n']}: склейка закадра кадров ≠ куску якоря")
        if len(_norm(unit["закадр"]).split()) >= len(group):
            for shot in group:
                if not _norm(shot.get("закадр")):
                    hard.append(f"кадр {shot.get('порядок')}: пустой закадр")
    stems: set[str] = set()
    prev: dict[str, Any] | None = None
    for shot in shots:
        n = shot.get("порядок")
        step = str(shot.get("действие") or "")
        if _COGNITIVE_ONLY_RE.search(step):
            hard.append(f"кадр {n}: вывод вместо видимого действия «{step[:40]}»")
        stem = action_stem(step)
        if stem in stems:
            soft.append(f"кадр {n}: повтор действия «{stem[:40]}»")
        stems.add(stem)
        for key, allowed in (
            ("план", COVERAGE_PLAN_CHOICES),
            ("ракурс", COVERAGE_ANGLE_CHOICES),
            ("движение", COVERAGE_MOVE_CHOICES),
            ("стык", STITCH_KEYS),
        ):
            if shot.get(key) not in allowed:
                hard.append(f"кадр {n}: {key} «{shot.get(key)}» не из списка")
        if prev is not None:
            a = _PLAN_STEP.get(str(prev.get("план") or ""), -1)
            b = _PLAN_STEP.get(str(shot.get("план") or ""), -1)
            if a >= 0 and b >= 0 and abs(a - b) >= 3 and prev.get("объект") == shot.get("объект"):
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
    if (
        len(shots) >= 2
        and shots[0].get("роль") != "вход"
        and shots[0].get("план") not in {"ОБЩИЙ", "ДАЛЬНИЙ", "ДЕТАЛЬ"}
    ):
        soft.append("первый кадр не вводит место (нет общего/детали входа)")
    return hard, soft


def build_improve_qc_prompt(
    *,
    parent: Frame,
    vo: str,
    shots: list[dict[str, Any]],
    units: list[dict[str, Any]],
    reasons: list[str],
) -> str:
    task = (
        "«Улучшить сцену»: QC кадров этой ячейки. Код нашёл брак:\n- "
        + "\n- ".join(reasons)
        + "\nВерни полный исправленный список `кадры` (с покрытием и `якорь_n`). "
        "Исходные события, порядок и якоря не меняй; закадр кадра — только из "
        "куска его якоря."
    )
    payload = {
        "frame_uuid": str(parent.uuid or ""),
        "frame_number": int(parent.number),
        "voiceover_text": vo,
        "якоря": _units_payload(units),
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
    units: list[dict[str, Any]],
    place: str,
    nodes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    fixed = repair_shots(shots, units)
    hard, soft = qc_reasons(shots, units)
    status = "fixed" if fixed else "ok"
    notes = list(fixed)
    if hard:
        try:
            reply = await ask(
                build_improve_qc_prompt(
                    parent=parent, vo=vo, shots=shots, units=units, reasons=hard
                )
            )
        except Exception as exc:  # noqa: BLE001
            reply = ""
            notes.append(f"GPT QC: {type(exc).__name__}"[:80])
        raw = _ops_fields(reply, str(parent.uuid or "")).get("кадры")
        if isinstance(raw, list) and raw:
            candidate = normalize_shots(
                raw, cell_number=int(parent.number), place=place, anchors=len(units)
            )
            if anchors_grouped(candidate, len(units)):
                extra = repair_shots(candidate, units)
                c_hard, c_soft = qc_reasons(candidate, units)
                if len(c_hard) < len(hard):
                    shots, hard, soft = candidate, c_hard, c_soft
                    notes.extend(extra)
                    notes.append("GPT QC исправил кадры")
                    status = "fixed"
        if hard:
            status = "warn"
    nodes.append(_node("fw_qc", status, "; ".join(notes + hard)[:400] or "брака нет"))
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
        theirs = _norm(gpt.get(ru) or gpt.get(key))
        if key == "light" and theirs and theirs not in COVERAGE_LIGHT_CHOICES:
            theirs = ""
        if key == "visual_type" and theirs and theirs not in COVERAGE_VISUAL_TYPE_CHOICES:
            theirs = ""
        value = mine if key in _LOCKED_KEYS and mine else (theirs or mine)
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


def _final_chain(shots: list[dict[str, Any]], units: list[dict[str, Any]], place: str) -> str:
    """главное_действие после QC: карточка N. на якорь, шаги = кадры, закадр = кусок."""
    return format_scene_chain(
        [
            {
                "n": u["n"],
                "place": place or _norm(_group(shots, u["n"])[0].get("место") if _group(shots, u["n"]) else ""),
                "action": " → ".join(_norm(s.get("действие")) for s in _group(shots, u["n"])),
                "vo": u["закадр"],
            }
            for u in units
            if _group(shots, u["n"])
        ]
    )


async def improve_cell_scene(
    session: AsyncSession,
    project: Project,
    frame_id: int,
    *,
    operator_prompt: str = "",
    passport: dict[str, Any] | None = None,
    anchors: list[Any] | None = None,
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
    vo = _norm(cell_full_text(parent, members))
    if not vo:
        raise RuntimeError("у сцены нет закадрового текста")
    op_passport = dict(passport or {})
    op_prompt = _norm(operator_prompt)
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
    nodes: list[dict[str, Any]] = []

    units = await stage_script(ask, parent, vo, op_prompt, nodes, anchors=anchors)
    cards, gpt_passport = await stage_action(
        ask,
        parent=parent,
        vo=vo,
        units=units,
        passport=pass_in,
        operator_prompt=op_prompt,
        nodes=nodes,
    )
    new_passport, changed = merge_passport(op_passport, gpt_passport)
    pass_after = _passport_input(new_passport, labels)
    place = pass_after.get("место") or ""
    shots = await stage_shots(
        ask,
        parent=parent,
        vo=vo,
        cards=cards,
        units=units,
        passport=pass_after,
        nodes=nodes,
    )
    shots, warnings = await stage_qc(
        ask,
        parent=parent,
        vo=vo,
        shots=shots,
        units=units,
        place=place,
        nodes=nodes,
    )
    if not shots:
        raise RuntimeError("улучшение не дало ни одного кадра")
    if _glue(shots) != vo:
        raise RuntimeError("закадр кадров не сошёлся с текстом якорей сцены — ничего не записано")

    applied = apply_cell_passport(parent, frames, new_passport)
    await session.flush()
    chain_text = _final_chain(shots, units, place)
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
        "anchors": [
            {"n": u["n"], "якорь": u["якорь"], "закадр": u["закадр"], "бюджет": u["бюджет"]}
            for u in units
        ],
        "shots": [
            {
                k: s.get(k)
                for k in (
                    "порядок",
                    "якорь_n",
                    "роль",
                    "действие",
                    "зона",
                    "объект",
                    "план",
                    "ракурс",
                    "движение",
                    "стык",
                    "закадр",
                )
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
            f"якорей {len(units)} · кадров {len(shots)} · +{apply_report.get('inserted_frames') or 0} новых"
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
        "montage improve #{} cell={} anchors={} shots={} inserted={} passport={} nodes={}",
        project.id,
        parent.number,
        len(units),
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
