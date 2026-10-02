"""Грамматика сцены → кадры: действие целиком, камера из таблицы, не T0–T10.

GPT пишет видимое действие сцены и объект шага. Код:
GPT пишет видимое действие сцены и объект шага. Код: камера из таблицы,
parent, место_новое. Авто-вставку «общий вид» не делаем. Каталог T/X не трогаем.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.services.shot_templates import parse_scene_chain, plain_scene_vo

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
        mild = ratio >= 0.7 or (missing_n <= 2 and ratio >= 0.5)
        if mild:
            soft = (
                f"WARN underproduction: GPT кадров {len(cleaned)} < ожидаемых {expected} "
                f"(steps {steps_raw}, VO-cap {cap if cap else 0}) — "
                "repair VO, без дописывания шагов действия"
            )
            return cleaned, soft
        return None, (
            f"GPT кадров {len(cleaned)} < ожидаемых {expected} "
            f"(steps {steps_raw}, VO-cap {cap if cap else 0}) "
            "— нужен добор GPT, код не дописывает шаги молча"
        )
    canonical = expand_action_to_shots(
        action, cell_number=cell_number, prev_place=prev_place
    )
    if not canonical:
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
    limit = expected if expected > 0 else cap
    if limit > 0 and len(out) > limit:
        kept: list[dict[str, Any]] = []
        normal_n = 0
        for s in out:
            if is_cutaway_shot(s) and normal_n >= limit:
                kept.append(s)
                continue
            if normal_n < limit:
                kept.append(s)
                if not is_cutaway_shot(s):
                    normal_n += 1
        out = kept or out[:limit]
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
            missing_vo = any(
                isinstance(s, dict) and not str(s.get("закадр") or "").strip()
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
                removed += 1
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
    """Перерезать закадр: >80 — дробим; <26 (не перебивка) — склеиваем и УДАЛЯЕМ пустышку.

    Предпочитаем меньше более длинных кусков в 26–80, а не десятки пустых
    оболочек после склейки. Хвост пустых non-cutaway выкидываем; число
    обычных кадров режем по VO-cap (floor(len/26)).
    Возвращает список предупреждений.
    """
    from app.services.scene_design.camera_expand import split_text_into_parts

    if not isinstance(shots, list) or not shots:
        return []
    warnings: list[str] = []
    norm_vo = " ".join((vo or "").split())
    cap = vo_shot_capacity(vo)

    # 0) underproduction grow ОТКЛЮЧЁН: пустые оболочки под VO давали empty_vo
    # и маскировали undercount. Severe undercount → PassStop/retry GPT,
    # а не invent оболочек без действия.
    # 1) короткие non-cutaway → склеить с соседом и pop (не оставлять пустой закадр)
    i = 0
    while i < len(shots):
        shot = shots[i]
        if not isinstance(shot, dict):
            i += 1
            continue
        chunk = str(shot.get("закадр") or "").strip()
        if not chunk:
            i += 1
            continue
        n = visible_len(chunk)
        if n < SHOT_VO_MIN and not is_cutaway_shot(shot) and len(shots) > 1:
            if i + 1 < len(shots) and isinstance(shots[i + 1], dict):
                nxt = str(shots[i + 1].get("закадр") or "").strip()
                shots[i + 1]["закадр"] = " ".join((chunk + " " + nxt).split()).strip()
                if not str(shots[i + 1].get("действие") or "").strip():
                    shots[i + 1]["действие"] = shot.get("действие") or ""
                shots.pop(i)
                warnings.append(f"склеили короткий закадр {n}->сосед (удалили пустышку)")
                continue
            if i > 0 and isinstance(shots[i - 1], dict):
                prev = str(shots[i - 1].get("закадр") or "").strip()
                shots[i - 1]["закадр"] = " ".join((prev + " " + chunk).split()).strip()
                shots.pop(i)
                warnings.append(f"склеили короткий закадр {n}<-сосед (удалили пустышку)")
                continue
        i += 1

    # 2) длиннее 80 — разрезать; куски кладём в пустые соседи
    idx = 0
    while idx < len(shots):
        shot = shots[idx]
        if not isinstance(shot, dict):
            idx += 1
            continue
        chunk = str(shot.get("закадр") or "").strip()
        if not chunk:
            idx += 1
            continue
        n = visible_len(chunk)
        if n <= SHOT_VO_MAX:
            idx += 1
            continue
        target = max(2, (n + SHOT_VO_IDEAL - 1) // SHOT_VO_IDEAL)
        if cap > 0:
            others = sum(
                1
                for j, s in enumerate(shots)
                if j != idx
                and isinstance(s, dict)
                and str(s.get("закадр") or "").strip()
                and not is_cutaway_shot(s)
            )
            room = max(1, cap - others)
            target = min(target, room)
        parts = [" ".join(p.split()) for p in split_text_into_parts(chunk, target)]
        parts = [p for p in parts if p]
        if len(parts) <= 1:
            warnings.append(f"кадр[{idx}] закадр {n} не удалось разрезать")
            idx += 1
            continue
        shot["закадр"] = parts[0]
        rest = parts[1:]
        ri = 0
        j = idx + 1
        while ri < len(rest) and j < len(shots):
            nxt = shots[j]
            if not isinstance(nxt, dict):
                j += 1
                continue
            cur = str(nxt.get("закадр") or "").strip()
            if not cur:
                nxt["закадр"] = rest[ri]
                ri += 1
            elif visible_len(cur) < SHOT_VO_MIN and not is_cutaway_shot(nxt):
                nxt["закадр"] = " ".join((rest[ri] + " " + cur).split())
                ri += 1
            j += 1
        if ri < len(rest):
            # Под VO-cap: плодим пустые оболочки только под VO (без invent действия).
            inserted = 0
            while ri < len(rest):
                normals_now = sum(
                    1
                    for s in shots
                    if isinstance(s, dict)
                    and str(s.get("закадр") or "").strip()
                    and not is_cutaway_shot(s)
                )
                if cap > 0 and normals_now >= cap:
                    break
                piece = rest[ri]
                ri += 1
                shell = {
                    "id": f"{shot.get('id') or 'S'}-v{idx}-{inserted}",
                    "действие": "",  # не invent шагов из narrative
                    "закадр": piece,
                    "объект": shot.get("объект") or "место",
                    "parent_id": shot.get("parent_id") or shot.get("id"),
                }
                shots.insert(idx + 1 + inserted, shell)
                inserted += 1
            if ri < len(rest):
                leftover = " ".join(rest[ri:])
                shot["закадр"] = " ".join(
                    (str(shot.get("закадр") or "") + " " + leftover).split()
                )
                warnings.append(
                    f"кадр[{idx}] закадр был {n}, хвост {visible_len(leftover)} приклеен (VO-cap)"
                )
            else:
                warnings.append(
                    f"кадр[{idx}] закадр {n} разрезан на {len(parts)} "
                    f"(+{inserted} оболочек VO)"
                )
            idx += 1 + inserted
        else:
            warnings.append(f"кадр[{idx}] закадр {n} разрезан на {len(parts)}")
            idx += 1

    # 3) выкинуть пустые non-cutaway оболочки (фикс «35 empty»)
    before = len(shots)
    kept: list[Any] = []
    for s in shots:
        if not isinstance(s, dict):
            kept.append(s)
            continue
        chunk = str(s.get("закадр") or "").strip()
        if chunk or is_cutaway_shot(s):
            kept.append(s)
    if len(kept) < before:
        warnings.append(f"удалили пустые закадры {before - len(kept)} шт")
        shots[:] = kept

    # 4) VO-cap: склеить лишние обычные в более длинные 26–80
    if cap > 0:
        while True:
            normals = [
                (i, s)
                for i, s in enumerate(shots)
                if isinstance(s, dict)
                and str(s.get("закадр") or "").strip()
                and not is_cutaway_shot(s)
            ]
            if len(normals) <= cap:
                break
            _li, last = normals[-1]
            _pi, prev = normals[-2]
            prev["закадр"] = " ".join(
                (
                    str(prev.get("закадр") or "").strip()
                    + " "
                    + str(last.get("закадр") or "").strip()
                ).split()
            )
            shots.pop(_li)
        if len(normals) > cap:
            warnings.append(f"сжали до VO-cap {cap} обычных кадров")

    glued = " ".join(
        str(s.get("закадр") or "").strip()
        for s in shots
        if isinstance(s, dict) and str(s.get("закадр") or "").strip()
    )
    if norm_vo and glued and " ".join(glued.split()) != norm_vo:
        warnings.append("после перерезки склейка != VO — флаг")
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
    norm_vo = " ".join((vo or "").split())
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
            covers = bool(norm_vo) and " ".join(chunk.split()) == norm_vo
            cell_short = len(shots) == 1 and visible_len(vo) < SHOT_VO_MIN
            cutaway = is_cutaway_shot(shot)
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
