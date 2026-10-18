"""Грамматика сцены → кадры: действие целиком, камера из таблицы, не T0–T10.

GPT пишет видимое действие сцены и объект шага. Код:
GPT пишет видимое действие сцены и объект шага. Код: камера из таблицы,
parent, место_новое. Авто-вставку «общий вид» не делаем. Каталог T/X не трогаем.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from loguru import logger

from app.services.shot_templates import parse_scene_chain, plain_scene_vo


def _mark_over_shot_limit(out: list[dict[str, Any]], limit: int) -> None:
    """Сверх лимита кадры остаются в списке с активен False."""
    if limit <= 0 or len(out) <= limit:
        return
    normal_n = 0
    for shot in out:
        over = False
        if is_cutaway_shot(shot) and normal_n >= limit:
            over = False
        elif normal_n < limit:
            if not is_cutaway_shot(shot):
                normal_n += 1
        else:
            over = True
        if over and shot.get("активен") is not False and not shot.get("карантин"):
            _mark_shot_kept(
                shot,
                f"сверх лимита кадров {limit} (VO-cap/expected) — кадр сохранён",
                where="merge_gpt_shots_with_action",
            )


def _mark_shot_kept(shot: dict[str, Any], reason: str, *, where: str) -> None:
    """Кадр остаётся в списке. Пометка и WARN вместо удаления."""
    shot["карантин"] = True
    shot["пометка"] = reason
    shot["активен"] = False
    logger.warning(
        "WARN {}: кадр {} помечен, не удалён: {}",
        where,
        shot.get("id") or shot.get("id_shot") or "?",
        reason,
    )


SHOT_VO_MIN = 26
SHOT_VO_MAX = 80
SHOT_VO_IDEAL = 45
SHOT_VO_CUTAWAY_MIN = 10
SHOT_VO_CUTAWAY_MAX = 30


def vo_shot_capacity(vo: str) -> int:
    """Сколько обычных кадров унесёт VO при мин. 26 симв.

    floor(len(VO)/26). Пустой → 0. Короткий (1..25) → 1.
    """
    n = visible_len(vo or "")
    if n <= 0:
        return 0
    if n < SHOT_VO_MIN:
        return 1
    return n // SHOT_VO_MIN


def expected_shots_for_cell(action: str, vo: str = "") -> int:
    """Скелет = шаги главное_действие; VO-cap — только если шагов нет.

    Не схлопываем фильм через min(steps, floor(len/26)): короткий VO
    режется на куски (в т.ч. перебивки 10–30), а не оправдывает 1–5 кадров.
    """
    steps = count_action_steps(action)
    if steps > 0:
        return steps
    return vo_shot_capacity(vo)


OBJECTS = ("место", "тело", "двое", "предмет", "лицо", "взгляд")

_STEP_SPLIT = re.compile(r"\s*→\s*|\s*->\s*")
_PLACE_RE = re.compile(
    r"вош[её]л|выш[её]л|вход|выход|приш[её]л|приехал|двор|кабинет|"
    r"улиц|комнат|зал|коридор|площад|отдел|офис|кухн|плац|арми",
    re.IGNORECASE,
)
_BODY_RE = re.compile(
    r"сел|сел[аи]|встал|ид[её]т|ш[её]л|шаг|стоит у|подош|подош[её]л|"
    r"подошла|снял|надева|переда|протяж",
    re.IGNORECASE,
)
_ITEM_RE = re.compile(
    r"открыл|открыва|взял|бер[её]т|папк|папк[уие]|печат|ключ|документ|"
    r"письм|жалоб|достал|доста[её]т|положил",
    re.IGNORECASE,
)
_FACE_RE = re.compile(
    r"лицо|прочитал|понял|поняла|взглянул на лицо|поднял (глаза|голову)|"
    r"испуга|усмех",
    re.IGNORECASE,
)
_PAIR_RE = re.compile(
    r"говор|спрашива|отвеча|диалог|двое|с плеча|допрос",
    re.IGNORECASE,
)
_LOOK_RE = re.compile(
    r"смотр|увидел|глядит|указыва|показыва",
    re.IGNORECASE,
)
_VISIBLE_VERB_RE = re.compile(
    r"вош|выш|сел|встал|ид[её]|ш[её]л|открыл|взял|бер|снял|надева|"
    r"полож|достал|смотрел|увидел|говор|шёл|шел|бежит|стоит|"
    r"смотр|меша|бега|ходит|кладет|кладёт|несёт|несет|держ|чита|"
    r"крич|пада|подн|опуск|брос|толк|тяне|обня|обним|переда|"
    r"протяж|открыва|закрыва|распахива|вбега|забега|подбега|выбега|"
    r"вход|заход|подход|выход|бег|шага",
    re.IGNORECASE,
)
_COGNITIVE_RE = re.compile(
    r"узнаёт|узнает|понимает|думает|чувствует|знает|помнит|"
    r"решил что|осознал",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"титр|имя|назван", re.IGNORECASE)
_THRESHOLD_RE = re.compile(
    r"двер|порог|калитк|ворот|открыва|открыл|распахн|толка\w* двер|"
    r"вбега|забега|врыва|ворва",
    re.IGNORECASE,
)


def is_threshold_step(step: str) -> bool:
    """Шаг на пороге: дверь, калитка, вбегает. Следующий кадр — продолжение, не новый общий."""
    return bool(_THRESHOLD_RE.search(step or ""))


def visible_len(text: str) -> int:
    return len("".join(c for c in (text or "") if unicodedata.category(c) != "Mn"))



_CUTAWAY_ROLES = frozenset({"перебивка", "реакция", "cutaway"})
_CUTAWAY_OBJS = frozenset({"лицо", "взгляд"})
_CUTAWAY_STEP_RE = re.compile(
    r"перебив|реакц|эмоц|мимика|кивок|усмех|испуг|взгляд",
    re.IGNORECASE,
)


def is_cutaway_shot(shot: dict[str, Any] | None) -> bool:
    """Перебивка / короткая реакция: 10–30 симв. или короткий эмо-кадр."""
    if not isinstance(shot, dict):
        return False
    role = str(shot.get("роль") or shot.get("role") or "").strip().casefold()
    if role in _CUTAWAY_ROLES:
        return True
    obj = str(shot.get("объект") or "").strip().casefold()
    if obj in _CUTAWAY_OBJS:
        return True
    step = str(shot.get("действие") or "")
    if _CUTAWAY_STEP_RE.search(step):
        return True
    plan = str(shot.get("план") or "").upper()
    if plan in {"ДЕТАЛЬ", "КРУПНЫЙ"} and obj in {"лицо", "взгляд", "предмет"}:
        return True
    return False


def action_stem(text: str) -> str:
    t = " ".join((text or "").split()).casefold()
    t = re.sub(r"^(общий|средний|крупный|деталь|дальний)\s+план\S*\s*", "", t)
    return t.strip(" .")


def split_scene_action(action: str) -> list[str]:
    raw = " ".join((action or "").split())
    if not raw:
        return []
    parts = [p.strip(" .") for p in _STEP_SPLIT.split(raw) if p.strip(" .")]
    return parts or [raw]


def classify_object(step: str) -> str:
    blob = step or ""
    if blob.casefold().startswith("общий вид"):
        return "место"
    if _PAIR_RE.search(blob):
        return "двое"
    if _ITEM_RE.search(blob):
        return "предмет"
    if _LOOK_RE.search(blob):
        return "взгляд"
    if _FACE_RE.search(blob):
        return "лицо"
    if _PLACE_RE.search(blob):
        return "место"
    if _BODY_RE.search(blob):
        return "тело"
    return "тело"


def has_visible_verb(action: str) -> bool:
    blob = action or ""
    if _TITLE_RE.search(blob):
        return True
    if _COGNITIVE_RE.search(blob) and not _VISIBLE_VERB_RE.search(blob):
        return False
    if _VISIBLE_VERB_RE.search(blob):
        return True
    obj = classify_object(blob)
    return obj in {"предмет", "место", "двое"} and not _COGNITIVE_RE.search(blob)


def object_matches_step(step: str, obj: str) -> bool:
    guessed = classify_object(step)
    want = (obj or "").strip().casefold()
    if want not in OBJECTS:
        return False
    if want == guessed:
        return True
    if want == "лицо" and guessed in {"предмет", "тело", "взгляд"}:
        return True
    return bool(want == "тело" and guessed == "место")


def fill_bit_spans(vo: str, bits: list[Any]) -> list[dict[str, Any]]:
    """Якорь = старт куска. Код режет закадр ячейки по якорям по порядку."""
    text = " ".join((vo or "").split())
    ordered = sorted(
        (dict(b) for b in bits if isinstance(b, dict)),
        key=lambda item: int(item.get("порядок") or 0),
    )
    if not text or not ordered:
        return ordered
    starts: list[int] = []
    cursor = 0
    for i, item in enumerate(ordered):
        anchor = " ".join(str(item.get("якорь") or item.get("закадр") or "").split())
        idx = -1
        if anchor:
            idx = text.find(anchor, cursor)
            if idx < 0:
                idx = text.casefold().find(anchor.casefold(), cursor)
        if idx < 0:
            starts.append(0 if i == 0 else cursor)
            continue
        starts.append(0 if i == 0 else idx)
        cursor = idx + max(len(anchor), 1)
    if not starts:
        return ordered
    for i, item in enumerate(ordered):
        start = starts[i] if i < len(starts) else 0
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        if end < start:
            end = start
        piece = text[start:end].strip()
        if piece:
            item["закадр"] = piece
        elif not str(item.get("закадр") or "").strip():
            item["закадр"] = anchor if i == 0 else ""
    joined = " ".join(str(b.get("закадр") or "").strip() for b in ordered)
    if " ".join(joined.split()) != text and ordered:
        from app.services.scene_design.camera_expand import split_text_into_parts

        parts = split_text_into_parts(text, len(ordered))
        for item, piece in zip(ordered, parts, strict=False):
            if piece:
                item["закадр"] = " ".join(piece.split())
    return ordered


def bits_cover_vo(vo: str, bits: list[Any]) -> bool:
    text = " ".join((vo or "").split())
    glued = " ".join(
        str(b.get("закадр") or "").strip()
        for b in bits
        if isinstance(b, dict) and str(b.get("закадр") or "").strip()
    )
    return " ".join(glued.split()) == text


def merge_same_place_scenes(action: str) -> str:
    """Соседние сцены с одним местом → одна карточка, шаги через →."""
    chain = parse_scene_chain(action)
    if len(chain) < 2:
        return action
    merged: list[dict[str, Any]] = []
    for scene in chain:
        place = str(scene.get("place") or "").strip().casefold()
        if (
            merged
            and place
            and place == str(merged[-1].get("place") or "").strip().casefold()
        ):
            prev = merged[-1]
            steps = split_scene_action(str(prev.get("action") or ""))
            steps.extend(split_scene_action(str(scene.get("action") or "")))
            kept: list[str] = []
            seen: set[str] = set()
            for step in steps:
                stem = action_stem(step)
                if stem and stem not in seen:
                    kept.append(step)
                    seen.add(stem)
            prev["action"] = " → ".join(kept)
            vo_a = plain_scene_vo(str(prev.get("vo") or ""))
            vo_b = plain_scene_vo(str(scene.get("vo") or ""))
            prev["vo"] = " ".join((vo_a + " " + vo_b).split())
            continue
        merged.append(dict(scene))
    lines: list[str] = []
    for i, scene in enumerate(merged, start=1):
        place = str(scene.get("place") or "").strip()
        act = str(scene.get("action") or "").strip()
        vo = str(scene.get("vo") or "").strip()
        head = f"{i}. {place} — {act}" if place else f"{i}. {act}"
        lines.append(head)
        if vo:
            if not (vo.startswith("(") and vo.endswith(")")):
                vo = f"({vo})"
            lines.append(vo)
    return "\n".join(lines)


def _split_vo_for_steps(vo: str, n: int) -> list[str]:
    """Сначала смысл (фразы/клаузы), потом доп. склейка кусков короче 26."""
    from app.services.scene_design.camera_expand import split_text_into_parts

    text = " ".join((vo or "").split())
    if n <= 1:
        return [text] if text else [""]
    if not text:
        return [""] * n
    parts = [" ".join((p or "").split()) for p in split_text_into_parts(text, n)]
    i = 0
    while i < len(parts):
        if visible_len(parts[i]) < SHOT_VO_MIN and len(parts) > 1:
            if i + 1 < len(parts):
                parts[i + 1] = " ".join((parts[i] + " " + parts[i + 1]).split())
                parts.pop(i)
                continue
            if i > 0:
                parts[i - 1] = " ".join((parts[i - 1] + " " + parts[i]).split())
                parts.pop(i)
                continue
        i += 1
    return parts or [text]


def _position(i: int, n: int, obj: str, place_new: bool) -> str:
    if i == 0 and place_new:
        return "вход"
    if i == n - 1 and obj in {"лицо", "предмет", "взгляд"}:
        return "пик"
    return "развитие"


_PACKS: dict[str, dict[str, str | int]] = {
    "место": {
        "план": "ОБЩИЙ",
        "линза_мм": 24,
        "высота": "уровень глаз",
        "наклон": "нейтральный",
        "точка": "фронт",
        "движение": "статика",
    },
    "тело": {
        "план": "СРЕДНИЙ",
        "линза_мм": 50,
        "высота": "уровень глаз",
        "наклон": "нейтральный",
        "точка": "3/4",
        "движение": "статика",
    },
    "двое": {
        "план": "СРЕДНИЙ",
        "линза_мм": 50,
        "высота": "с плеча",
        "наклон": "нейтральный",
        "точка": "из-за плеча",
        "движение": "статика",
    },
    "предмет": {
        "план": "ДЕТАЛЬ",
        "линза_мм": 85,
        "высота": "сверху",
        "наклон": "сверху вниз",
        "точка": "фронт",
        "движение": "статика",
    },
    "лицо": {
        "план": "КРУПНЫЙ",
        "линза_мм": 85,
        "высота": "уровень глаз",
        "наклон": "нейтральный",
        "точка": "фронт",
        "движение": "статика",
    },
    "взгляд": {
        "план": "СРЕДНЕ-КРУПНЫЙ",
        "линза_мм": 50,
        "высота": "уровень глаз",
        "наклон": "нейтральный",
        "точка": "3/4",
        "движение": "статика",
    },
}


_SIT_ENTER_RE = re.compile(
    r"вош[её]л|выш[её]л|вбеж|влет|вход|подош[её]л|приш[её]л|ид[её]т|бежит",
    re.IGNORECASE,
)
_SIT_DIALOG_RE = re.compile(
    r"говор|спрашив|отвеча|кивн|слуша|шепч|крич",
    re.IGNORECASE,
)
_SIT_REACT_RE = re.compile(
    r"ужас|испуг|улыб|плач|злоб|стыд|шокир|осозна|понял|поняла",
    re.IGNORECASE,
)
_SIT_PROP_RE = re.compile(
    r"открыл|закрыл|положил|достал|взял|штамп|печат|кнопк|папк|письм|лист",
    re.IGNORECASE,
)


def choose_plan_for_situation(
    *,
    obj: str,
    place_new: bool,
    position: str,
    step: str = "",
) -> str:
    """План по ситуации (docs/plan_rules_script_frames_qc.md), не только объект→план."""
    blob = step or ""
    if place_new:
        return "ОБЩИЙ"
    if position == "вход" and _SIT_ENTER_RE.search(blob):
        return "ОБЩИЙ"
    if obj == "предмет" or (_SIT_PROP_RE.search(blob) and obj in {"", "тело", "предмет"}):
        if _SIT_PROP_RE.search(blob) and not re.search(
            r"тянет|тянется|подход", blob, re.IGNORECASE
        ):
            return "ДЕТАЛЬ"
        return "СРЕДНИЙ"
    if obj == "лицо" or _SIT_REACT_RE.search(blob):
        return "КРУПНЫЙ"
    if obj == "взгляд":
        return "СРЕДНЕ-КРУПНЫЙ"
    if obj == "двое" or _SIT_DIALOG_RE.search(blob):
        return "СРЕДНЕ-КРУПНЫЙ" if _SIT_DIALOG_RE.search(blob) else "СРЕДНИЙ"
    if obj == "место" and not place_new:
        return "СРЕДНИЙ"
    if obj == "тело" and position == "пик":
        return "СРЕДНЕ-КРУПНЫЙ"
    if obj == "тело" and _SIT_ENTER_RE.search(blob):
        return "ОБЩИЙ"
    key = obj if obj in _PACKS else "тело"
    return str(_PACKS[key]["план"])


def camera_pack(
    *,
    obj: str,
    place_new: bool,
    position: str,
    hod: bool,
    prev: dict[str, Any] | None,
    step: str = "",
) -> dict[str, Any]:
    key = obj if obj in _PACKS else "тело"
    pack = dict(_PACKS[key])
    plan = choose_plan_for_situation(
        obj=obj, place_new=place_new, position=position, step=step
    )
    pack["план"] = plan
    if plan in {"ДАЛЬНИЙ", "ОБЩИЙ"}:
        pack["линза_мм"] = 24
        pack["точка"] = "фронт"
    elif plan == "СРЕДНИЙ":
        pack["линза_мм"] = 35 if obj == "место" else 50
        pack["точка"] = "3/4"
    elif plan == "СРЕДНЕ-КРУПНЫЙ":
        pack["линза_мм"] = 50
    elif plan in {"КРУПНЫЙ", "ДЕТАЛЬ"}:
        pack["линза_мм"] = 85
        if plan == "ДЕТАЛЬ":
            pack["высота"] = "сверху"
            pack["наклон"] = "сверху вниз"
            pack["точка"] = "фронт"
    if position == "вход" and place_new:
        pack["точка"] = "фронт"
    if position == "пик" and obj == "лицо":
        pack["движение"] = "наезд"
    if hod:
        pack["движение"] = "следование"
    pack["стык"] = "рез по жесту" if prev else "прямая склейка"
    if prev:
        same_plan = str(prev.get("план") or "") == str(pack["план"])
        same_mm = int(prev.get("линза_мм") or 0) == int(pack["линза_мм"])
        same_point = str(prev.get("точка") or "") == str(pack["точка"])
        if same_plan and same_mm and same_point:
            pack["точка"] = "3/4" if pack["точка"] == "фронт" else "с плеча"
    pack["ракурс"] = f"{pack['высота']}, {pack['наклон']}, {pack['точка']}"
    return pack


def _hod(step: str) -> bool:
    return bool(
        re.search(
            r"ид[её]т|ш[её]л|ехал|бежит|вош[её]л|выш[её]л|приш",
            step or "",
            re.IGNORECASE,
        )
    )


def expand_action_to_shots(
    action: str,
    *,
    cell_number: int = 1,
    prev_place: str = "",
) -> list[dict[str, Any]]:
    """Карточки сцен → кадры. Камера из таблицы. Без авто-«общий вид»."""
    chain = parse_scene_chain(action)
    if not chain:
        return []
    out: list[dict[str, Any]] = []
    last_place = (prev_place or "").strip().casefold()
    order = 0
    last_step = ""
    for scene in chain:
        place = str(scene.get("place") or "").strip()
        act = str(scene.get("action") or "").strip()
        vo = plain_scene_vo(str(scene.get("vo") or ""))
        place_new = bool(place) and place.casefold() != last_place
        if not has_visible_verb(act):
            steps = [act] if act else ["действие сцены"]
        else:
            steps = split_scene_action(act)
        uniq: list[str] = []
        seen: set[str] = set()
        for step in steps:
            stem = action_stem(step)
            if stem and stem not in seen:
                uniq.append(step)
                seen.add(stem)
        steps = uniq or steps[:1] or ["действие"]
        pieces = _split_vo_for_steps(vo, len(steps))
        if len(pieces) < len(steps):
            # длинный закадр лучше держать на одном кадре, чем дробить в обрубки
            vo_text = " ".join(vo.split())
            if visible_len(vo_text) > SHOT_VO_MAX:
                pieces = [vo_text] + [""] * (len(steps) - 1)
            else:
                pieces = pieces + [""] * (len(steps) - len(pieces))
        else:
            pieces = pieces[: len(steps)]
        n = len(steps)
        master_id = f"{cell_number}-S{int(scene['n'])}-K1"
        prev_shot: dict[str, Any] | None = None
        # Прошли через дверь прошлой карточки — изнутри продолжаем, не новый общий.
        through_door = place_new and bool(out) and is_threshold_step(last_step)
        for i in range(n):
            step = steps[i]
            obj = classify_object(step)
            entering = place_new and not (i == 0 and through_door)
            pos = _position(i, n, obj, entering)
            pack = camera_pack(
                obj=obj,
                place_new=entering,
                position=pos,
                hod=_hod(step),
                prev=prev_shot,
                step=step,
            )
            if i == 0 and through_door:
                pack["стык"] = "рез по жесту"
            order += 1
            sid = f"{cell_number}-S{int(scene['n'])}-K{i + 1}"
            shot = {
                "id": sid,
                "parent_id": None if i == 0 else master_id,
                "порядок": order,
                "сцена": int(scene["n"]),
                "место": place,
                "действие": step,
                "объект": obj,
                "позиция": pos,
                "закадр": pieces[i] if i < len(pieces) else "",
                "план": pack["план"],
                "линза_мм": pack["линза_мм"],
                "ракурс": pack["ракурс"],
                "высота": pack["высота"],
                "наклон": pack["наклон"],
                "точка": pack["точка"],
                "движение": pack["движение"],
                "стык": pack["стык"],
                "свет": "",
            }
            out.append(shot)
            prev_shot = shot
            last_step = step
        if place:
            last_place = place.casefold()
    return out


def fill_kadry_scene_numbers(shots: list[Any]) -> int:
    """Проставить ``сцена``, если GPT выкинул номер, но оставил parent_id.

    Без этого promote_shots_to_vo_cells режет каждый кадр в свою VO-ячейку.
    """
    filled = 0
    scene = 0
    master: dict[str, int] = {}
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        raw = shot.get("сцена")
        if raw not in (None, ""):
            try:
                scene = int(raw)
            except (TypeError, ValueError):
                scene = scene or 1
                shot["сцена"] = scene
                filled += 1
            sid = str(shot.get("id") or "").strip()
            if sid:
                master[sid] = scene
            continue
        pid = shot.get("parent_id")
        sid = str(shot.get("id") or "").strip()
        if pid in (None, "", "null"):
            scene += 1
            shot["сцена"] = scene
            filled += 1
            if sid:
                master[sid] = scene
        else:
            sc = master.get(str(pid).strip()) or scene or 1
            shot["сцена"] = sc
            filled += 1
            if sid:
                master[sid] = sc
    return filled


def count_action_steps(action: str) -> int:
    """Сколько видимых шагов в главное_действие (без авто-вставок места)."""
    chain = parse_scene_chain(action)
    n = 0
    for scene in chain:
        act = str(scene.get("action") or "").strip()
        if not act:
            continue
        if not has_visible_verb(act):
            n += 1
            continue
        steps = split_scene_action(act)
        stems: set[str] = set()
        for step in steps:
            stem = action_stem(step)
            if stem and stem not in stems:
                stems.add(stem)
                n += 1
        if not stems and act:
            n += 1
    return n




def _prefer_full_action_text(*candidates: str) -> str:
    """Выбрать полную цепь ОДНОЙ сцены, не stub и не dump всех сцен."""
    best = ""
    best_score = (-1, -999, -1, -1, -1)
    for raw in candidates:
        s = str(raw or "").strip()
        if not s:
            continue
        arrows = s.count("→") + s.count("->")
        scenes = len(re.findall(r"(?m)^\s*\d+\.\s", s))
        numbered = 1 if re.match(r"^\s*\d+\.", s) else 0
        multi_pen = scenes if scenes > 1 else 0
        sc = (1 if arrows else 0, -multi_pen, arrows, numbered, len(s))
        if sc > best_score:
            best, best_score = s, sc
    return best


def merge_gpt_shots_with_action(
    gpt_shots: list[Any],
    action: str,
    *,
    cell_number: int = 1,
    prev_place: str = "",
    vo: str = "",
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Склейка GPT с шагами действия.

    Если шаг совпал — берём текст GPT (действие/закадр/объект/…).
    Если GPT вернул 0 кадров — ошибка (PassStop).
    Mild undercount (≥70% / ≤2 дырки) — WARN soft.
    Severe undercount — None + «нужен добор GPT» (PassStop/retry), без invent.
    """
    dict_shots = [dict(s) for s in gpt_shots if isinstance(s, dict)]
    if not dict_shots:
        return None, "GPT не вернул кадры — нужен добор или повтор ноды"
    # убрать наследие авто-«общий вид …»
    cleaned = [
        s
        for s in dict_shots
        if not str(s.get("действие") or "").casefold().startswith("общий вид")
    ]
    if not cleaned:
        cleaned = dict_shots
    steps_raw = count_action_steps(action)
    cap = vo_shot_capacity(vo)
    expected = expected_shots_for_cell(action, vo)
    # Undercount: mild → WARN; severe → PassStop (retry), без invent шагов.
    # Severe: <50% expected или меньше expected-2 при expected≥5.
    if expected > 0 and len(cleaned) < expected:
        if len(cleaned) < 1:
            return None, (
                f"GPT кадров 0 < ожидаемых {expected} "
                f"(steps {steps_raw}, VO-cap {cap if cap else 0}) "
                "— нужен добор GPT, код не дописывает шаги молча"
            )
        missing_n = expected - len(cleaned)
        ratio = len(cleaned) / float(expected)
        # Было hard-stop при severe (<70%). С 2026-10-06: любой недобор
        # при got>=1 — soft WARN (как до b1b8b454 PassStop), без добора.
        soft = (
            f"WARN underproduction: GPT кадров {len(cleaned)} < ожидаемых {expected} "
            f"(steps {steps_raw}, VO-cap {cap if cap else 0}, "
            f"missing={missing_n}, ratio={ratio:.2f}) — "
            "repair VO, без дописывания шагов действия"
        )
        return cleaned, soft
    canonical = expand_action_to_shots(
        action, cell_number=cell_number, prev_place=prev_place
    )
    if not canonical:
        _mark_over_shot_limit(cleaned, expected if expected > 0 else cap)
        return cleaned, None
    by_stem: dict[str, dict[str, Any]] = {}
    for s in cleaned:
        stem = action_stem(str(s.get("действие") or ""))
        if stem and stem not in by_stem:
            by_stem[stem] = s
    out: list[dict[str, Any]] = []
    missing: list[str] = []
    for i, can in enumerate(canonical):
        stem = action_stem(str(can.get("действие") or ""))
        gpt = by_stem.get(stem)
        if gpt is None and i < len(cleaned):
            # порядок совпал — тоже считаем матчем
            gpt = cleaned[i]
            gpt_stem = action_stem(str(gpt.get("действие") or ""))
            if gpt_stem and stem and gpt_stem != stem and stem not in by_stem:
                # чужой шаг на этой позиции — не матч
                if len(cleaned) < expected:
                    gpt = None
        if gpt is None:
            missing.append(str(can.get("действие") or "")[:60])
            continue
        row = dict(can)
        for key in (
            "действие",
            "закадр",
            "объект",
            "люди",
            "камера",
            "меняет",
            "точность",
            "старт",
            "конец",
            "план",
            "ракурс",
            "стык",
            "движение",
        ):
            val = gpt.get(key)
            if val not in (None, "", [], {}):
                row[key] = val
        for key in ("id", "parent_id", "порядок", "сцена", "место"):
            if gpt.get(key) not in (None, ""):
                row[key] = gpt[key]
        out.append(row)
    if missing:
        # Mild: почти все шаги покрыты (≤2 дырки и ≥70% canonical) → WARN.
        # Severe → PassStop на retry, без silent invent и без принятия 1/N.
        n_can = max(1, len(canonical))
        covered = n_can - len(missing)
        ratio = covered / float(n_can)
        mild = (
            len(cleaned) >= 1
            and len(missing) <= 2
            and ratio >= 0.7
            and covered >= 1
        )
        if mild:
            soft = (
                f"WARN underproduction: GPT не покрыл {len(missing)} шагов действия "
                f"({'; '.join(missing[:5])}) — пишем покрытие "
                f"(кадров {len(out or cleaned)}), repair VO, без invent шагов"
            )
            keep = out if out else cleaned
            return keep, soft
        return None, (
            "GPT не покрыл шаги действия: "
            + "; ".join(missing[:5])
            + " — нужен добор GPT"
        )
    # если GPT дал больше кадров (осознанные вставки) — дописать хвост GPT
    if len(cleaned) > len(out):
        seen_ids = {str(r.get("id") or "") for r in out}
        for s in cleaned[len(out) :]:
            sid = str(s.get("id") or "")
            if sid and sid in seen_ids:
                continue
            out.append(s)
    # VO-cap / expected: не держим десятки кадров на короткий закадр.
    _mark_over_shot_limit(out, expected if expected > 0 else cap)
    return out, None


def apply_grammar_to_ops(ops: list[Any], frames: list[dict[str, Any]]) -> None:
    """Дозаполнить камеру; склеить GPT с шагами. Пустые/неполные — не молчать."""
    by_uid = {
        str(fr.get("uuid") or "").strip(): fr
        for fr in frames
        if isinstance(fr, dict) and str(fr.get("uuid") or "").strip()
    }
    prev_place = ""
    for op in ops:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields")
        if not isinstance(fields, dict):
            continue
        uid = str(op.get("frame_uuid") or "").strip()
        fr = by_uid.get(uid) or {}
        attrs = fr.get("attrs") if isinstance(fr.get("attrs"), dict) else {}
        action = _prefer_full_action_text(
            fields.get("главное_действие"),
            fields.get("main_action"),
            fr.get("главное_действие"),
            fr.get("main_action"),
            attrs.get("главное_действие") if isinstance(attrs, dict) else "",
            attrs.get("main_action") if isinstance(attrs, dict) else "",
        )
        shots = fields.get("кадры")
        fields.pop("_shots_incomplete", None)
        vo = ""
        if isinstance(fr, dict):
            vo = str(fr.get("voiceover_text") or fr.get("закадр") or "").strip()
            attrs = fr.get("attrs") if isinstance(fr.get("attrs"), dict) else {}
            if not vo and isinstance(attrs, dict):
                vo = str(
                    attrs.get("закадр") or attrs.get("voiceover_text") or ""
                ).strip()
        if action and isinstance(shots, list) and shots:
            num = int(fr.get("number") or 1)
            merged, err = merge_gpt_shots_with_action(
                shots, action, cell_number=num, prev_place=prev_place, vo=vo
            )
            if err and str(err).startswith("WARN"):
                # Не кладём в fields: db_apply.reject unknown. Маркер на op.
                op["_shots_under_warn"] = err
                fields.pop("_shots_under_warn", None)
                if merged is not None:
                    fields["кадры"] = merged
                    shots = merged
                # soft underproduction: не incomplete, не invent шагов
            elif err:
                fields["_shots_incomplete"] = err
                # оставляем GPT как есть — вызывающий код обязан упасть/добрать
            elif merged is not None:
                fields["кадры"] = merged
                shots = merged
        elif action and (not isinstance(shots, list) or len(shots) < 1):
            fields["_shots_incomplete"] = (
                "GPT не вернул кадры — нужен добор или повтор ноды "
                "(код не разворачивает шаги действие молча)"
            )
        if isinstance(shots, list) and shots:
            # Пустой закадр у части кадров — норма (пауза/перебивка, промт §4).
            # Раскладываем VO по кадрам, только если GPT не дал закадр ни одному
            # кадру; иначе куски ложились поверх уже разложенных — дубли.
            missing_vo = not any(
                isinstance(s, dict) and str(s.get("закадр") or "").strip()
                for s in shots
            )
            if vo and missing_vo:
                pieces = _split_vo_for_steps(vo, len(shots))
                for shot, piece in zip(shots, pieces, strict=False):
                    if isinstance(shot, dict) and not str(shot.get("закадр") or "").strip():
                        shot["закадр"] = piece
            _fill_missing_camera(shots, prev_place=prev_place)
            fill_kadry_scene_numbers(shots)
            for sh in shots:
                if isinstance(sh, dict) and sh.get("место"):
                    prev_place = str(sh.get("место") or "")
        chain = parse_scene_chain(action)
        if chain:
            last = str(chain[-1].get("place") or "").strip()
            if last:
                prev_place = last


def _fill_missing_camera(shots: list[Any], *, prev_place: str = "") -> None:
    prev: dict[str, Any] | None = None
    last_place = (prev_place or "").strip().casefold()
    n = len(shots)
    for i, shot in enumerate(shots):
        if not isinstance(shot, dict):
            continue
        step = str(shot.get("действие") or shot.get("action") or "")
        obj = str(shot.get("объект") or "").strip().casefold()
        if obj not in OBJECTS:
            obj = classify_object(step)
            shot["объект"] = obj
        place = str(shot.get("место") or shot.get("place") or "").strip()
        place_new = bool(place) and place.casefold() != last_place
        if i == 0 and not place:
            place_new = not last_place
        if place_new and prev is not None and is_threshold_step(
            str(prev.get("действие") or prev.get("action") or "")
        ):
            place_new = False
        pos = str(shot.get("позиция") or "") or _position(i, n, obj, place_new)
        pack = camera_pack(
            obj=obj,
            place_new=place_new,
            position=pos,
            hod=_hod(step),
            prev=prev,
            step=step,
        )
        shot.setdefault("план", pack["план"])
        shot.setdefault("линза_мм", pack["линза_мм"])
        shot.setdefault("ракурс", pack["ракурс"])
        shot.setdefault("высота", pack["высота"])
        shot.setdefault("наклон", pack["наклон"])
        shot.setdefault("точка", pack["точка"])
        shot.setdefault("движение", pack["движение"])
        shot.setdefault("стык", pack["стык"])
        if i == 0:
            shot["parent_id"] = None
        elif not str(shot.get("parent_id") or "").strip():
            first = shots[0] if isinstance(shots[0], dict) else {}
            shot["parent_id"] = first.get("id")
        if place:
            last_place = place.casefold()
        prev = shot


def _title_chunk(chunk: str, words: list[str]) -> bool:
    if any(q in chunk for q in ("«", "»", "“", "”")):
        return True
    return bool(words) and all(w[:1].isupper() for w in words)


def _vo_pieces_splintered(pieces: list[str]) -> bool:
    from app.services.scene_design.camera_expand import vo_chunk_is_dangling

    for chunk in pieces:
        if not chunk:
            continue
        if vo_chunk_is_dangling(chunk):
            return True
        words = re.findall(r"[^\W\d_]+", chunk, flags=re.UNICODE)
        if 1 <= len(words) <= 2 and not _title_chunk(chunk, words):
            return True
    return False


def _assign_vo_by_steps(vo: str, steps: list[str]) -> list[str]:
    """Закадр по шагам. Обрубок и слово-осколок («судов») схлопываются на первый кадр."""
    from app.services.scene_design.camera_expand import split_text_into_parts

    text = " ".join((vo or "").split())
    n = len(steps)
    if n <= 0:
        return [text] if text else []
    if not text:
        return [""] * n
    pieces = [" ".join(p.split()) for p in split_text_into_parts(text, n)]
    if len(pieces) < n:
        pieces.extend([""] * (n - len(pieces)))
    pieces = pieces[:n]
    glued = " ".join(p for p in pieces if p)
    if " ".join(glued.split()) != text or _vo_pieces_splintered(pieces):
        return [text] + [""] * (n - 1)
    return pieces


def _same_place(left: str, right: str) -> bool:
    a = " ".join((left or "").casefold().split())
    b = " ".join((right or "").casefold().split())
    if not a or not b:
        return True
    return a == b or a in b or b in a


def _frame_action(op: dict[str, Any], frame: dict[str, Any]) -> str:
    fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
    attrs = frame.get("attrs") if isinstance(frame.get("attrs"), dict) else {}
    return _prefer_full_action_text(
        fields.get("главное_действие"),
        fields.get("main_action"),
        frame.get("главное_действие"),
        frame.get("main_action"),
        attrs.get("главное_действие") if isinstance(attrs, dict) else "",
        attrs.get("main_action") if isinstance(attrs, dict) else "",
    )


def align_shots_to_action_chain(
    ops: list[Any], frames: list[dict[str, Any]]
) -> int:
    """Убрать кадры чужой комнаты. Кадры того же места, даже вне цепи, остаются."""
    by_uid = {
        str(fr.get("uuid") or "").strip(): fr
        for fr in frames
        if isinstance(fr, dict) and str(fr.get("uuid") or "").strip()
    }
    removed = 0
    for op in ops:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields")
        if not isinstance(fields, dict):
            continue
        shots = fields.get("кадры")
        if not isinstance(shots, list) or not shots:
            continue
        uid = str(op.get("frame_uuid") or "").strip()
        chain = parse_scene_chain(_frame_action(op, by_uid.get(uid) or {}))
        places: dict[int, str] = {}
        for scene in chain:
            try:
                n = int(scene.get("n") or 0)
            except (TypeError, ValueError):
                continue
            place = str(scene.get("place") or "").strip()
            if n and place:
                places[n] = place
        if not places:
            continue
        kept: list[Any] = []
        for shot in shots:
            if not isinstance(shot, dict):
                kept.append(shot)
                continue
            try:
                n = int(shot.get("сцена") or 0)
            except (TypeError, ValueError):
                n = 0
            scene_place = places.get(n) or ""
            shot_place = str(shot.get("место") or "").strip()
            if scene_place and shot_place and not _same_place(shot_place, scene_place):
                if shot.get("активен") is not False and not shot.get("карантин"):
                    _mark_shot_kept(
                        shot,
                        f"чужое место «{shot_place}» ≠ сцена «{scene_place}» — кадр сохранён",
                        where="align_shots_to_action_chain",
                    )
                    removed += 1
                kept.append(shot)
                continue
            kept.append(shot)
        if len(kept) != len(shots):
            fields["кадры"] = kept
    return removed


def scene_script_text(place: str, action: str) -> str:
    """Блок «Сценарий сцены»: место и шаги (без авто-кадра «общий вид»)."""
    where = " ".join((place or "").split())
    steps = split_scene_action(action)
    bits = ["Сценарий сцены."]
    if where:
        bits.append(f"Место «{where}»: сначала ориентация в пространстве, затем действие.")
    if steps:
        bits.append("Шаги: " + " → ".join(steps) + ".")
    else:
        rest = " ".join((action or "").split())
        if rest:
            bits.append(rest)
    return " ".join(bits)



def repair_shot_vo_lengths(shots: list[Any], vo: str) -> list[str]:
    """Перерезать закадр НА МЕСТЕ (тот же список кадров ячейки), без дублей.

    >80 — режем; хвост кладём в следующие пустые кадры по порядку (порядок
    речи не меняется). <26 (не перебивка) — переносим в ближайший непустой
    кусок, если склейка ≤80; кадр со шагом остаётся с пустым закадром
    (пауза между кусками — норма по промту). Текст никогда не копируется:
    кусок переносится, исходный кадр очищается. Удаляем только пустые
    оболочки без шага, без закадра и без промтов.
    Возвращает список предупреждений.
    """
    from app.services.scene_design.camera_expand import split_text_into_parts

    if not isinstance(shots, list) or not shots:
        return []
    warnings: list[str] = []
    norm_vo = " ".join((vo or "").split())

    def _vo(s: Any) -> str:
        return str(s.get("закадр") or "").strip() if isinstance(s, dict) else ""

    def _join(a: str, b: str) -> str:
        return " ".join((a + " " + b).split()).strip()

    def _reply(s: Any) -> bool:
        # guard: закадр никогда не кладём на кадр с непустой «реплика»
        return isinstance(s, dict) and bool(str(s.get("реплика") or "").strip())

    # 1) длиннее 80 — разрезать; куски в следующие пустые кадры до первого непустого
    idx = 0
    while idx < len(shots):
        shot = shots[idx]
        chunk = _vo(shot)
        n = visible_len(chunk)
        if not chunk or n <= SHOT_VO_MAX:
            idx += 1
            continue
        empties: list[int] = []
        j = idx + 1
        while j < len(shots):
            if not isinstance(shots[j], dict):
                j += 1
                continue
            if _vo(shots[j]):
                break
            if _reply(shots[j]):
                j += 1
                continue
            empties.append(j)
            j += 1
        nxt_i = j if j < len(shots) and not _reply(shots[j]) else None
        target = max(2, (n + SHOT_VO_IDEAL - 1) // SHOT_VO_IDEAL)
        target = min(target, 1 + len(empties)) if empties else target
        parts = [" ".join(p.split()) for p in split_text_into_parts(chunk, target)]
        parts = [p for p in parts if p]
        if len(parts) <= 1:
            warnings.append(f"кадр[{idx}] закадр {n} не удалось разрезать")
            idx += 1
            continue
        if not empties:
            # Нет пустого слота: хвост — в начало следующего куска, если влезет.
            if nxt_i is not None and len(parts) == 2 and visible_len(
                _join(parts[1], _vo(shots[nxt_i]))
            ) <= SHOT_VO_MAX and visible_len(parts[0]) >= SHOT_VO_MIN:
                shot["закадр"] = parts[0]
                shots[nxt_i]["закадр"] = _join(parts[1], _vo(shots[nxt_i]))
                warnings.append(f"кадр[{idx}] закадр {n}: хвост перенесён в следующий кусок")
            else:
                warnings.append(f"кадр[{idx}] закадр {n} >80, пустого кадра рядом нет — оставили")
            idx += 1
            continue
        shot["закадр"] = parts[0]
        rest = parts[1:]
        slots = empties[: len(rest)]
        for k, sj in enumerate(slots):
            if k == len(slots) - 1 and len(rest) > len(slots):
                shots[sj]["закадр"] = " ".join(rest[k:])
            else:
                shots[sj]["закадр"] = rest[k]
        warnings.append(f"кадр[{idx}] закадр {n} разрезан на {1 + len(slots)}")
        idx = (slots[-1] if slots else idx) + 1

    # 2) короткие (<26, не перебивка) → в ближайший непустой кусок, кадр оставляем
    i = 0
    while i < len(shots):
        shot = shots[i]
        chunk = _vo(shot)
        n = visible_len(chunk)
        if not chunk or n >= SHOT_VO_MIN or is_cutaway_shot(shot):
            i += 1
            continue
        nxt = next((j for j in range(i + 1, len(shots)) if _vo(shots[j])), None)
        prv = next((j for j in range(i - 1, -1, -1) if _vo(shots[j])), None)
        if nxt is not None and _reply(shots[nxt]):
            nxt = None
        if prv is not None and _reply(shots[prv]):
            prv = None
        if nxt is not None and visible_len(_join(chunk, _vo(shots[nxt]))) <= SHOT_VO_MAX:
            shots[nxt]["закадр"] = _join(chunk, _vo(shots[nxt]))
            shot["закадр"] = ""
            warnings.append(f"короткий закадр {n} перенесён в следующий кусок (кадр оставлен)")
        elif prv is not None and visible_len(_join(_vo(shots[prv]), chunk)) <= SHOT_VO_MAX:
            shots[prv]["закадр"] = _join(_vo(shots[prv]), chunk)
            shot["закадр"] = ""
            warnings.append(f"короткий закадр {n} перенесён в предыдущий кусок (кадр оставлен)")
        i += 1

    # 3) пустые оболочки помечаем, не выкидываем. Реплика и прочие
    # содержательные поля — не пустота: карантин спрятал бы кадр.
    _content_keys = (
        "действие",
        "реплика",
        "говорит",
        "кто",
        "позы",
        "где",
        "эмоции",
        "фон",
        "старт",
        "конец",
    )
    empty_n = 0
    for s in shots:
        if not isinstance(s, dict):
            continue
        if (
            not _vo(s)
            and not any(str(s.get(k) or "").strip() for k in _content_keys)
            and not any(str(s.get(k) or "").strip() for k in s if str(k).startswith("промт"))
            and not s.get("предметы")
            and not s.get("люди")
        ):
            if not s.get("карантин"):
                _mark_shot_kept(
                    s,
                    "пустая оболочка без шага, закадра и промтов — кадр сохранён",
                    where="repair_shot_vo_lengths",
                )
                empty_n += 1
    if empty_n:
        warnings.append(f"помечены пустые оболочки без шага {empty_n} шт")

    glued = " ".join(_vo(s) for s in shots if _vo(s))
    if norm_vo and glued and " ".join(glued.split()) != norm_vo:
        warnings.append("после перерезки склейка != VO — флаг")
    # R-VO-SENTENCE: закадр режется ТОЛЬКО по концу предложения (.!?…);
    # всё порезанное выше по словам/запятым собирается обратно в целые предложения.
    if norm_vo and glued and " ".join(glued.split()) == norm_vo:
        warnings.extend(reflow_vo_by_sentences(shots, norm_vo))
    return warnings


def shots_grammar_reason(shots: list[Any], vo: str, uid: str = "") -> str | None:
    prefix = f"uuid {uid[:8]}: " if uid else ""
    if not shots:
        return f"{prefix}пустые кадры"
    if not any(
        isinstance(s, dict) and str(s.get("закадр") or "").strip() for s in shots
    ):
        return (
            f"{prefix}кадр пустой закадр "
            "— у каждого кадра свой кусок"
        )
    stems: set[str] = set()
    prev: dict[str, Any] | None = None
    for shot in shots:
        if not isinstance(shot, dict):
            continue
        step = str(shot.get("действие") or "")
        if not has_visible_verb(step) and step:
            return f"{prefix}шаг без видимого глагола «{step[:40]}»"
        obj = str(shot.get("объект") or "").strip().casefold()
        if obj and obj not in OBJECTS:
            return f"{prefix}объект «{obj}» не из enum"
        if obj and step and not object_matches_step(step, obj):
            return f"{prefix}объект «{obj}» не сходится с «{step[:40]}»"
        mm = shot.get("линза_мм")
        plan = str(shot.get("план") or "")
        if obj == "лицо" and mm in (18, 24):
            return f"{prefix}лицо нельзя на {mm}mm"
        if obj == "предмет" and "из-за плеча" in str(
            shot.get("ракурс") or shot.get("точка") or ""
        ):
            return f"{prefix}предмет нельзя OTS"
        if obj == "место" and mm in (85, 135) and "КРУПН" in plan.upper():
            return f"{prefix}место нельзя крупным {mm}mm"
        chunk = str(shot.get("закадр") or "").strip()
        if chunk:
            n = visible_len(chunk)
            cell_short = len(shots) == 1 and visible_len(vo) < SHOT_VO_MIN
            cutaway = is_cutaway_shot(shot)
            # Точка в конце не отменяет норму длины: целая фраза длиннее 80
            # или короче 26 (не перебивка) — всё ещё «симв».
            if n > SHOT_VO_MAX:
                return (
                    f"{prefix}кадр закадр {n} симв. "
                    f"(разрежь, не вали весь VO; норма {SHOT_VO_MIN}–{SHOT_VO_MAX})"
                )
            if n < SHOT_VO_CUTAWAY_MIN and not (cutaway or cell_short):
                return (
                    f"{prefix}кадр закадр {n} симв. "
                    f"(<{SHOT_VO_CUTAWAY_MIN} только у перебивки/реакции)"
                )
            if (
                n < SHOT_VO_MIN
                and not cutaway
                and not cell_short
            ):
                return (
                    f"{prefix}кадр закадр {n} симв. "
                    f"(норма {SHOT_VO_MIN}–{SHOT_VO_MAX}; "
                    f"перебивка {SHOT_VO_CUTAWAY_MIN}–{SHOT_VO_CUTAWAY_MAX})"
                )
            if (
                cutaway
                and SHOT_VO_CUTAWAY_MIN <= n <= SHOT_VO_CUTAWAY_MAX
            ):
                pass  # ок: перебивка 10–30
            elif cutaway and n < SHOT_VO_CUTAWAY_MIN:
                pass  # ок: короткий эмо/реакция кадр
        stem = action_stem(step)
        if stem:
            if stem in stems:
                return f"{prefix}повтор действия «{stem[:50]}»"
            stems.add(stem)
        if prev:
            same = (
                str(prev.get("план") or "") == plan
                and str(prev.get("точка") or "") == str(shot.get("точка") or "")
                and int(prev.get("линза_мм") or 0) == int(mm or 0)
            )
            if same and action_stem(str(prev.get("действие") or "")) == stem:
                return f"{prefix}два одинаковых кадра подряд"
        prev = shot
    glued = " ".join(
        str(s.get("закадр") or "").strip()
        for s in shots
        if isinstance(s, dict) and str(s.get("закадр") or "").strip()
    )
    cell = " ".join((vo or "").split())
    if cell and glued and " ".join(glued.split()) != cell:
        return f"{prefix}склейка закадр кадров ≠ voiceover_text"
    return None


# ---- R-VO-SENTENCE (c61): закадр режется ТОЛЬКО по концу предложения ----
import re as _re
_SENT_END = _re.compile(r'(?<=[.!?…])["»)]*\s+(?=["«(]?[A-ZА-ЯЁ0-9—–-])')
_END_OK = _re.compile(r'[.!?…]["»)]*\s*$')


def split_vo_sentences(vo):
    """VO → предложения (склейка через пробел = исходный VO)."""
    t = " ".join(str(vo or "").split())
    if not t:
        return []
    out, pos = [], 0
    for m in _SENT_END.finditer(t):
        out.append(t[pos:m.start()].strip())
        pos = m.end()
    out.append(t[pos:].strip())
    return [s for s in out if s]


def vo_piece_ends_sentence(piece):
    return bool(_END_OK.search(" ".join(str(piece or "").split())))


def reflow_vo_by_sentences(shots, vo):
    """Детерминированно: каждое предложение VO целиком — в кадр, где оно началось
    (по текущей раскладке), на кадрах с репликой закадра нет (предложение уходит на
    ближайший предыдущий кадр без реплики, иначе — следующий). Склейка = VO."""
    rows = [s for s in shots if isinstance(s, dict)]
    sents = split_vo_sentences(vo)
    if not rows or not sents:
        return []
    norm = " ".join(sents)

    def v(s):
        return " ".join(str(s.get("закадр") or "").split())

    def rep(s):
        return bool(str(s.get("реплика") or "").strip())

    # позиция начала каждого куска в склейке
    starts, pos = [], 0
    for i, s in enumerate(rows):
        if v(s):
            starts.append((pos, i))
            pos += len(v(s)) + 1
    free = [i for i, s in enumerate(rows) if not rep(s)]
    if not free:
        return ["нет кадров без реплики под закадр"]
    owner = {}
    p = 0
    for k, sent in enumerate(sents):
        own = None
        for st, i in starts:
            if st <= p:
                own = i
        if own is None:
            own = free[0]
        if rep(rows[own]):
            prev = [j for j in free if j < own]
            nxt = [j for j in free if j > own]
            own = prev[-1] if prev else nxt[0]
        # порядок не должен идти назад
        if k and owner[k - 1] > own:
            own = owner[k - 1]
        owner[k] = own
        p += len(sent) + 1
    new = {i: "" for i in range(len(rows))}
    for k, sent in enumerate(sents):
        i = owner[k]
        new[i] = (new[i] + " " + sent).strip()
    notes = []
    for i, s in enumerate(rows):
        if v(s) != new[i]:
            notes.append(f"{s.get('id')}: закадр по предложениям")
            s["закадр"] = new[i]
    glued = " ".join(v(s) for s in rows if v(s))
    if glued != norm:
        raise RuntimeError("reflow: склейка закадра не сошлась с предложениями")
    return notes


def vo_sentence_problems(shots, vo):
    """Гейт: кусок закадра = целые предложения; нет закадра на кадре реплики;
    реплика не стоит внутри предложения (гарантируется первым)."""
    out = []
    rows = [s for s in shots if isinstance(s, dict)]
    pieces = [(s, " ".join(str(s.get("закадр") or "").split())) for s in rows]
    nonempty = [(s, t) for s, t in pieces if t]
    cell = " ".join(str(vo or "").split())
    # Без конца предложения в VO ячейки резать «по предложениям» нечего:
    # куски вроде «кусок закадра первый» — нормальная склейка, не брак.
    if not cell or _END_OK.search(cell) or _SENT_END.search(cell):
        for s, t in nonempty[:-1]:
            if not vo_piece_ends_sentence(t):
                out.append(f"{s.get('id')}: закадр рвёт предложение «…{t[-30:]}»")
    for s, t in pieces:
        if t and str(s.get("реплика") or "").strip():
            out.append(f"{s.get('id')}: закадр и реплика в одном кадре")
    return out


# ---- R-CHAR-ID (c61): персонаж из базы — всегда с id (вариант образа — id варианта) ----
def _strip_acc(t):
    return str(t or "").replace("\u0301", "")


def _char_patterns(cards, scene_ids=None):
    """[(id, имя, regex)]. Вариант (правила = id родителя, имя-заглушка) берёт имя родителя;
    если в сцене есть вариант родителя, а самого родителя нет — имя родителя → id варианта."""
    by = {str(c.get("id") or c.get("code") or "").strip(): c for c in cards or []}
    scene = [str(x).strip() for x in (scene_ids or []) if str(x).strip()]
    parent = {cid: str(c.get("правила") or "").strip() for cid, c in by.items()
              if str(c.get("правила") or "").strip() in by and str(c.get("правила") or "").strip() != cid}
    out = []
    for cid, c in by.items():
        if cid in parent:
            continue
        nm = _strip_acc(c.get("имя") or c.get("name") or "").strip()
        words = [w for w in nm.split() if len(w) >= 4 and w.isalpha() and w[0].isupper()]
        if not cid or not words:
            continue
        use = cid
        if cid not in scene:
            var = [v for v in scene if parent.get(v) == cid]
            if var:
                use = var[0]
        family = [cid] + [v for v, p in parent.items() if p == cid]
        stems = sorted({(w[:-1] if w[-1] in "аяйоеьи" else w) for w in words}, key=len, reverse=True)
        alt = "|".join(_re.escape(x) for x in stems)
        rx = _re.compile(r"(?<![\wА-Яа-яЁё])(?:" + alt + r")[а-яё]*(?:\s+(?:" + alt + r")[а-яё]*)?", _re.I)
        out.append((use, nm, rx, family))
    return out


def _norm_scene_ids(scene_ids):
    """2026-10-11: «персонажи» may come as a string 'c01, c07' -> list of ids."""
    if scene_ids is None:
        return None
    if isinstance(scene_ids, str):
        return [x for x in re.findall(r'c\d+', scene_ids)]
    return [str(x).strip() for x in scene_ids if str(x).strip()]


def ensure_character_ids(shots, cards, scene_ids=None):
    scene_ids = _norm_scene_ids(scene_ids)
    """Первое упоминание персонажа базы в каждом поле промта/кто без id → «Имя (cNN)»;
    чужой id семьи (c01 вместо варианта c05 этой сцены) → id сцены. `говорит` = имя → id."""
    pats = _char_patterns(cards, scene_ids)
    notes = []
    for s in shots or []:
        if not isinstance(s, dict):
            continue
        for key in [k for k in list(s) if str(k).startswith("промт")] + ["кто", "действие", "позы", "где"]:
            txt = s.get(key)
            if not isinstance(txt, str) or not txt:
                continue
            new = txt
            for cid, nm, rx, fam in pats:
                for f in fam:
                    if f != cid:
                        new = _re.sub(r"\(\s*(?:id\s*)?" + f + r"\s*\)", f"({cid})", new)
                        new = _re.sub(r"\bid\s+" + f + r"\b", f"id {cid}", new)
                if key not in ("кто",) and not str(key).startswith("промт"):
                    continue
                if cid in _strip_acc(new):
                    continue
                m = rx.search(new)
                if m:
                    new = new[:m.end()] + f" ({cid})" + new[m.end():]
            if new != txt:
                s[key] = new
                notes.append(f"{s.get('id')}: id в {key}")
        who = _strip_acc(str(s.get("говорит") or "")).strip()
        for cid, nm, rx, fam in pats:
            if who and who != cid and (rx.fullmatch(who) or who in fam):
                s["говорит"] = cid
                notes.append(f"{s.get('id')}: говорит → {cid}")
        for p in s.get("люди") or []:
            if isinstance(p, dict) and isinstance(p.get("кто"), str):
                t = p["кто"]
                for cid, nm, rx, fam in pats:
                    for f in fam:
                        if f != cid:
                            t = _re.sub(r"\(\s*" + f + r"\s*\)", f"({cid})", t)
                    t = _re.sub(r"(\(" + cid + r"\))(\s*\(" + cid + r"\))+", r"\1", t)
                p["кто"] = t
                w = _strip_acc(str(p.get("кто") or ""))
                for cid, nm, rx, fam in pats:
                    if w and not any(f in w for f in fam) and rx.search(w):
                        p["кто"] = f"{p['кто']} ({cid})"
    return notes


def character_id_problems(shots, cards, scene_ids=None):
    scene_ids = _norm_scene_ids(scene_ids)
    out = []
    pats = _char_patterns(cards, scene_ids)
    if scene_ids is not None:
        allowed = set()
        for cid, nm, rx, fam in pats:
            if cid in [str(x) for x in scene_ids]:
                allowed |= set(fam)
        allowed |= {str(x) for x in scene_ids}
        blob = _strip_acc(" ".join(" ".join(str(v) for k, v in s.items() if str(k).startswith("промт") or k in ("кто", "говорит", "действие")) for s in shots or [] if isinstance(s, dict)))
        known = {str(c.get("id")) for c in cards or []}
        extra = sorted({m for m in _re.findall(r"\bc\d\d\b", blob) if m in known and m not in allowed})
        if extra:
            out.append(f"в ячейке нет персонажей {', '.join(extra)} (персонажи = {list(scene_ids)}): роли без карточки не подменяй персонажами базы")
    for s in shots or []:
        if not isinstance(s, dict):
            continue
        txt = _strip_acc(" ".join(str(v) for k, v in s.items() if str(k).startswith("промт")) + " " + str(s.get("кто") or ""))
        for cid, nm, rx, fam in pats:
            if rx.search(txt) and cid not in txt:
                out.append(f"{s.get('id')}: «{nm}» без id {cid}")
            for f in fam:
                if f != cid and _re.search(r"\b" + f + r"\b", txt):
                    out.append(f"{s.get('id')}: id {f}, а в этой ячейке образ {cid}")
                    break
    return out




# ---- R-REPLY-CAM (c62): камера кадров реплик по §3, после всех правок плана/30° ----
_PLAN_RX = _re.compile(r"План\s+(ДАЛЬНИЙ|ОБЩИЙ|СРЕДНЕ-КРУПНЫЙ|СРЕДНИЙ|КРУПНЫЙ|ДЕТАЛЬ)")
_ANG_RX = _re.compile(r"Ракурс\s+[^.]*\.")
_HGT_RX = _re.compile(r"Высота\s+(снизу вверх|сверху вниз|уровень глаз)")


def _ang(v):
    t = str(v or "").strip().casefold()
    for k in ("с плеча", "фронт", "3/4", "сверху", "снизу", "макро"):
        if t.startswith(k):
            return k
    return t


def _set_cam_text(s, plan, ang_sentence):
    for k in [k for k in s if str(k).startswith("промт") and k != "промт_видео"]:
        t = s.get(k)
        if not isinstance(t, str) or not t:
            continue
        t = _PLAN_RX.sub(f"План {plan}", t, count=1)
        t = _ANG_RX.sub(ang_sentence, t, count=1)
        t = _HGT_RX.sub("Высота уровень глаз", t, count=1)
        s[k] = t


def normalize_reply_cameras(shots):
    """Кадр реплики: `с плеча` (через плечо слушающего на говорящего) или монолог КРУПНЫЙ фронт.
    Подряд идущие реплики разных людей — встречная пара: оба `с плеча`, один план СРЕДНЕ-КРУПНЫЙ."""
    rows = [s for s in shots or [] if isinstance(s, dict)]
    reps = [s for s in rows if str(s.get("реплика") or "").strip()]
    notes = []
    for idx, s in enumerate(reps):
        who = str(s.get("говорит") or "").strip()
        others = {str(r.get("говорит") or "").strip().casefold() for r in reps} - {who.casefold(), ""}
        plan = str(s.get("план") or "").strip().upper()
        ang = _ang(s.get("ракурс"))
        legal = ang == "с плеча" or (plan == "КРУПНЫЙ" and ang == "фронт")
        neighbours = [reps[j] for j in (idx - 1, idx + 1) if 0 <= j < len(reps)]
        in_pair = any(str(n.get("говорит") or "").strip().casefold() != who.casefold() for n in neighbours)
        if legal and not (in_pair and (ang != "с плеча" or plan != "СРЕДНЕ-КРУПНЫЙ")):
            continue
        if not others and not in_pair:
            new_plan, new_ang, sent = "КРУПНЫЙ", "фронт", f"Ракурс фронт — камера спереди на говорящего {who}."
        else:
            lst = next((str(n.get("говорит") or "") for n in neighbours if str(n.get("говорит") or "").strip().casefold() != who.casefold()), "") or (sorted(others)[0] if others else "собеседника")
            new_plan, new_ang, sent = "СРЕДНЕ-КРУПНЫЙ", "с плеча", f"Ракурс с плеча — через плечо {lst} на говорящего {who}."
        before = (s.get("план"), s.get("ракурс"))
        s["план"], s["ракурс"], s["высота"] = new_plan, new_ang, "уровень глаз"
        if "точка" in s:
            s["точка"] = "с плеча" if new_ang == "с плеча" else "фронт"
        if str(s.get("объект") or "") not in ("двое", "лицо"):
            s["объект"] = "двое" if new_ang == "с плеча" else "лицо"
        _set_cam_text(s, new_plan, sent)
        notes.append(f"{s.get('id')}: камера реплики {before} → ({new_plan}, {new_ang})")
    return notes


# ---- R-POSE-CARRY (c62): поза прошлого кадра — в промт текущего ----
def carry_pose(shots):
    rows = [s for s in shots or [] if isinstance(s, dict)]
    notes = []
    for i in range(1, len(rows)):
        p, s = rows[i - 1], rows[i]
        if p.get("место") != s.get("место") or str(s.get("кто") or "").strip().casefold() in ("", "никого", "нет"):
            continue
        act = str(s.get("действие") or "")
        if _UP.search(act):
            continue
        if _SIT.search(_prompts_text(s)) or _STANDS.search(str(s.get("позы") or "")):
            continue
        clause = ""
        for src, f in ((s, "позы"), (p, "позы"), (p, "конец"), (p, "действие")):
            for part in _re.split(r"[;.]\s*", str(src.get(f) or "")):
                if _SIT.search(part):
                    clause = part.strip()
                    break
            if clause:
                break
        if not clause:
            continue
        add = f"Поза из прошлого кадра сохраняется: {clause}."
        if not _SIT.search(str(s.get("позы") or "")):
            s["позы"] = (str(s.get("позы") or "").rstrip(" .") + ("; " if s.get("позы") else "") + clause).strip()
        for k in ("промт_старт", "промт_картинки"):
            if isinstance(s.get(k), str) and s[k].strip():
                s[k] = s[k].rstrip() + " " + add
                break
        notes.append(f"{s.get('id')}: поза перенесена «{clause[:40]}»")
    return notes



# ---- R-CONTINUITY (c61): заметил → кадр объекта; поза; внутри/снаружи ----
_NOTICE = _re.compile(r"\b(замеча\w*|заметил\w*|видит|увидел\w*|смотрит на|смотрит в|разглядыва\w*|обнаружива\w*|обнаружил\w*|находит|нашёл|нашел|рассматрива\w*)\b", _re.I)
_SIT = _re.compile(r"\b(сел|села|сидит|сидя|садится|усажива\w*|присел\w*|приседа\w*|на корточках|лёг|лег|легла|лежит|лёжа|лежа)\b", _re.I)
_UP = _re.compile(r"\b(вста\w*|поднима\w*ся|поднял\w*ся|выпрямл\w*|идёт|идет|шагает|выходит|уходит|входит|уводит)\b", _re.I)
_EXIT = _re.compile(r"\b(выходит|вышел|выйдя|покидает|покинул)\w*\s+(из|на)\s+(здани|подъезд|дом|отдел|улиц|двор|крыльц)", _re.I)
_INSIDE = _re.compile(r"(дверь комнаты|дверь кабинета|в коридоре|в комнате|в кабинете|внутри здания|открывает дверь|«комната)", _re.I)
_STANDS = _re.compile(r"\b(стоит|стоя|стоят|встал\w*|встаёт|поднят\w*|выпрям\w*)\b", _re.I)
_ENTER = _re.compile(r"\b(входит|вошёл|вошел|заходит|возвращается|заводят|вводят)\b", _re.I)


def _prompts_text(s):
    return " ".join(str(v) for k, v in s.items() if str(k).startswith("промт"))


def shots_continuity_problems(shots):
    rows = [s for s in shots or [] if isinstance(s, dict)]
    out = []
    exited = False
    for i, s in enumerate(rows):
        act = str(s.get("действие") or "")
        sid = s.get("id") or f"кадр {i + 1}"
        _mn = _NOTICE.search(act)
        if _mn and not _re.search(r"\bне\s+$", act[:_mn.start()]):
            n = rows[i + 1] if i + 1 < len(rows) else None
            nobody = n is not None and str(n.get("кто") or "").strip().casefold() in ("", "никого", "нет")
            ok = (n is not None and (str(n.get("план") or "").upper() in ("ДЕТАЛЬ", "КРУПНЫЙ") or nobody)
                  and n.get("объект") in ("предмет", "взгляд", "место") and not _NOTICE.search(str(n.get("действие") or "")))
            if not ok:
                out.append(f"{sid}: «{_mn.group(0)}» — следующим кадром нужна вставка ДЕТАЛЬ/КРУПНЫЙ того, что он видит (POV)")
        if i and rows[i - 1].get("место") == s.get("место") and str(s.get("кто") or "").strip().casefold() not in ("", "никого", "нет"):
            p = rows[i - 1]
            ptxt = " ".join(str(p.get(k) or "") for k in ("действие", "конец", "позы"))
            if _SIT.search(ptxt) and not _UP.search(act) and not _STANDS.search(str(s.get("позы") or "")) and not _SIT.search(_prompts_text(s)):
                out.append(f"{sid}: в прошлом кадре сидит/присел/лежит — поза не перенесена в промт")
        if exited and _INSIDE.search(act + " " + str(s.get("где") or "")) and not _ENTER.search(act):
            out.append(f"{sid}: вышел из здания, а теперь снова внутри без кадра входа")
            exited = False
        if _EXIT.search(act):
            exited = True
        elif _ENTER.search(act):
            exited = False
    return out


# ---- R-MERGE-TRIVIAL / R-VO-SPREAD (2026-10-15) ----
_POV_OBJ = {"взгляд"}
_NOTICE_RE = _re.compile(r"замеча|замети|видит|увидел|смотрит на|разгляд|обнаруж|наход|нашёл|нашел|читает", _re.I)
MERGE_MAX_ACTION_CHARS = 240
_CONT_RE = _re.compile(r"оставаясь|продолжа|не вставая|не отпуская|всё ещё|все еще|затем|сидя |стоя |держа ", _re.I)


def _shot_s(s, k):
    return " ".join(str((s or {}).get(k) or "").split())


def _who_key(s):
    t = _shot_s(s, "кто").lower().replace("ё", "е")
    ids = sorted(set(_re.findall(r"c\d{2}", t)))
    if ids:
        return ",".join(ids)
    return ",".join(sorted(x.strip() for x in _re.split(r"[,;]| и ", t) if x.strip()))


def _subject_key(s):
    a = _shot_s(s, "действие").lower().replace("ё", "е")
    ids = _re.findall(r"c\d{2}", a[:80])
    if ids:
        return ids[0]
    w = _re.findall(r"[а-яa-z]+", a)
    return w[0] if w else ""


def _is_reply(s):
    return bool(_shot_s(s, "реплика")) or bool(_shot_s(s, "говорит"))


def _can_merge_trivial(a, b, nxt):
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return False
    if _is_reply(a) or _is_reply(b):
        return False  # один говорящий на кадр, реплика — свой кадр
    if nxt is not None and _is_reply(nxt):
        return False  # b — общий/установочный перед диалогом
    if _shot_s(b, "объект") in _POV_OBJ or not _shot_s(b, "кто"):
        return False  # POV-вставка «что заметил» — отдельный кадр
    if _NOTICE_RE.search(_shot_s(a, "действие")):
        return False  # за «заметил» идёт POV — не склеивать
    if _shot_s(a, "закадр") and _shot_s(b, "закадр"):
        return False  # два куска закадра — два кадра (не меняем раскладку VO)
    pa = (_shot_s(a, "место") or _shot_s(a, "зона")).lower()
    pb = (_shot_s(b, "место") or _shot_s(b, "зона")).lower()
    if pa != pb:
        return False
    if _who_key(a) != _who_key(b) or not _who_key(a):
        return False
    if _subject_key(a) != _subject_key(b) or not _subject_key(a):
        return False
    same_cam = _shot_s(a, "план") == _shot_s(b, "план") and _shot_s(a, "ракурс") == _shot_s(b, "ракурс")
    cont = bool(_CONT_RE.search(_shot_s(b, "действие")[:80]))
    trivial = same_cam or cont or _shot_s(b, "объект") == _shot_s(a, "объект")
    if not trivial:
        return False
    if len(_shot_s(a, "действие")) + len(_shot_s(b, "действие")) > MERGE_MAX_ACTION_CHARS:
        return False
    return True


def merge_trivial_shots(shots):
    """Склеить соседние кадры: то же место, те же люди, тот же субъект, та же
    камера (или тот же объект) и простое продолжение действия. Не трогает
    реплики, POV после «заметил», установочный перед диалогом. Закадр не теряется."""
    if not isinstance(shots, list) or len(shots) < 2:
        return []
    from app.orchestrator.validators.activity_flags import shot_is_active

    inactive_rows = [
        shot
        for shot in shots
        if isinstance(shot, dict) and not shot_is_active(shot)
    ]
    live = [shot for shot in shots if isinstance(shot, dict) and shot_is_active(shot)]
    if len(live) < 2:
        return []
    notes = []
    out = [live[0]]
    merged_into = {}
    chain = 1
    for i in range(1, len(live)):
        a, b = out[-1], live[i]
        nxt = live[i + 1] if i + 1 < len(live) else None
        if chain < 2 and _can_merge_trivial(a, b, nxt):
            act = _shot_s(a, "действие").rstrip(".")
            a["действие"] = (act + "; " + _shot_s(b, "действие")).strip()
            if _shot_s(b, "конец") and not _shot_s(a, "конец"):
                a["конец"] = b.get("конец")
            # Закадр склеенного кадра не выбрасывается: пустой сосед забирает
            # текст, два непустых куска склеиваются (склейка = оба текста).
            vo_a = _shot_s(a, "закадр")
            vo_b = _shot_s(b, "закадр")
            if vo_b and not vo_a:
                a["закадр"] = b.get("закадр")
            elif vo_a and vo_b and vo_b not in vo_a:
                a["закадр"] = f"{vo_a} {vo_b}".strip()
            if _shot_s(b, "точность") == "старт/конец":
                a["точность"] = "старт/конец"
            merged_into[str(b.get("id") or "")] = str(a.get("id") or "")
            notes.append(f"{b.get('id')} склеен в {a.get('id')}")
            chain += 1
            continue
        out.append(b)
        chain = 1
    if not notes:
        return []
    for k, s in enumerate(out, start=1):
        if isinstance(s, dict):
            if "порядок" in s:
                s["порядок"] = k
            pid = str(s.get("parent_id") or "")
            if pid in merged_into:
                s["parent_id"] = merged_into[pid]
    from app.services.frame_tombstones import merge_kadry_keep_missing

    # Неактивные строки идут мимо merge и возвращаются в хвост как есть:
    # совпадение их id с живым после перенумерации не гасит живой шот.
    old_live = [
        shot for shot in shots if not isinstance(shot, dict) or shot_is_active(shot)
    ]
    shots[:] = [*merge_kadry_keep_missing(old_live, out), *inactive_rows]
    return notes


def spread_vo_sentences(shots):
    """Если в одном кадре сидят несколько предложений закадра, а кадров без
    реплики хватает — разнести целые предложения по кадрам ячейки равномерно
    (порядок сохраняется, предложение не режется, на кадр с репликой не кладём).
    Склейка непустых кусков = исходный закадр."""
    rows = [s for s in shots if isinstance(s, dict)] if isinstance(shots, list) else []
    if len(rows) < 2:
        return []
    pieces = [_shot_s(s, "закадр") for s in rows]
    full = " ".join(p for p in pieces if p)
    sents = split_vo_sentences(full)
    nonempty = [i for i, p in enumerate(pieces) if p]
    if len(sents) <= len(nonempty):
        return []
    free = [i for i, s in enumerate(rows) if not _is_reply(s)]
    if len(free) < 2:
        return []
    start = nonempty[0] if nonempty else free[0]
    free = [i for i in free if i >= start] or free
    n, m = len(sents), len(free)
    owner = []
    for k in range(n):
        owner.append(free[min(m - 1, (k * m) // n)])
    new = {i: "" for i in range(len(rows))}
    for k, sent in enumerate(sents):
        new[owner[k]] = (new[owner[k]] + " " + sent).strip()
    if " ".join(new[i] for i in range(len(rows)) if new[i]) != full:
        return []
    notes = []
    for i, s in enumerate(rows):
        if pieces[i] != new[i]:
            s["закадр"] = new[i]
            notes.append(f"{s.get('id')}: закадр разнесён по предложениям")
    return notes


# ---- R-WIDE-BEFORE-DIALOG (2026-10-15): общий установочный план перед диалогом ----
_KID_RE = _re.compile(r"^(.*-K)(\d+)$")


def ensure_wide_before_dialogue(shots):
    """Перед блоком реплик двух+ говорящих должен стоять ОБЩИЙ кадр без реплики.
    Если его нет (перед диалогом деталь/POV/крупный) — вставляем установочный
    ОБЩИЙ · фронт на обоих, без закадра и реплики; id K перенумеровываются."""
    import copy

    if not isinstance(shots, list) or not shots:
        return []
    notes = []
    i = 0
    while i < len(shots):
        s = shots[i]
        if not (isinstance(s, dict) and _is_reply(s)) or (i > 0 and isinstance(shots[i - 1], dict) and _is_reply(shots[i - 1])):
            i += 1
            continue
        j = i
        speakers = set()
        while j < len(shots) and isinstance(shots[j], dict) and _is_reply(shots[j]):
            speakers.add(_shot_s(shots[j], "говорит").casefold())
            j += 1
        prev = shots[i - 1] if i > 0 and isinstance(shots[i - 1], dict) else None
        if len(speakers) >= 2 and not (prev is not None and _shot_s(prev, "план").upper() == "ОБЩИЙ"):
            w = copy.deepcopy(s)
            who = _shot_s(s, "кто") or "участники разговора"
            w["действие"] = f"{who} друг напротив друга перед разговором, видно, кто где."
            for k in ("говорит", "реплика", "закадр", "эмоции"):
                if k in w or k in ("говорит", "реплика", "закадр"):
                    w[k] = ""
            w["план"], w["ракурс"], w["высота"], w["объект"] = "ОБЩИЙ", "фронт", "уровень глаз", "двое"
            w["точность"] = "одна картинка"
            if "точка" in w:
                w["точка"] = "фронт"
            _set_cam_text(w, "ОБЩИЙ", "Ракурс фронт — камера спереди, видны оба собеседника.")
            for key in (
                "image_prompt",
                "animation_prompt",
                "промт_картинки",
                "промт_видео",
                "промт",
                "media",
                "png",
                "video",
                "child_prompts",
            ):
                w.pop(key, None)
            used = {str(row.get("id") or "") for row in shots if isinstance(row, dict)}
            m_id = _KID_RE.match(str(s.get("id") or ""))
            if m_id:
                stem = m_id.group(1)
                max_k = 0
                for row in shots:
                    if not isinstance(row, dict):
                        continue
                    rm = _KID_RE.match(str(row.get("id") or ""))
                    if not rm or rm.group(1) != stem:
                        continue
                    try:
                        max_k = max(max_k, int(rm.group(2)))
                    except ValueError:
                        pass
                cand = f"{stem}{max_k + 1}"
                while cand in used:
                    max_k += 1
                    cand = f"{stem}{max_k + 1}"
            else:
                base = str(s.get("id") or "wide")
                n_id = 1
                cand = f"{base}-K{n_id}"
                while cand in used:
                    n_id += 1
                    cand = f"{base}-K{n_id}"
            w["id"] = cand
            if "порядок" in w:
                for row in shots[i:]:
                    if not isinstance(row, dict) or "порядок" not in row:
                        continue
                    try:
                        row["порядок"] = int(row["порядок"]) + 1
                    except (TypeError, ValueError):
                        pass
            shots.insert(i, w)
            notes.append(f"{s.get('id')}: вставлен ОБЩИЙ установочный перед диалогом")
            i = j + 1
            continue
        i = j
    return notes
