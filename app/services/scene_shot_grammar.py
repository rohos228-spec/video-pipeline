"""Грамматика сцены → кадры: действие целиком, камера из таблицы, не T0–T10.

GPT пишет видимое действие сцены и объект шага. Код:
режет закадр 13–80 (цель ~45), подставляет камеру, parent, место_новое.
Каталог T/X для монтажа не трогаем.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from app.services.shot_templates import parse_scene_chain, plain_scene_vo

SHOT_VO_MIN = 13
SHOT_VO_MAX = 80
SHOT_VO_IDEAL = 45

OBJECTS = ("место", "тело", "двое", "предмет", "лицо", "взгляд")

_STEP_SPLIT = re.compile(r"\s*→\s*|\s*->\s*")
_PLACE_RE = re.compile(
    r"вош[её]л|выш[её]л|вход|выход|приш[её]л|приехал|двор|кабинет|"
    r"улиц|комнат|зал|коридор|площад|отдел|офис|кухн|плац|арми",
    re.IGNORECASE,
)
_BODY_RE = re.compile(
    r"сел|сел[аи]|встал|вста[её]т|ид[её]т|ш[её]л|шаг|стоит у|стоя[тл]|подош|подош[её]л|"
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
    r"вош|выш|сел|встал|вста[её]т|ид[её]|ш[её]л|открыл|взял|бер|снял|надева|"
    r"полож|достал|смотрел|увидел|говор|шёл|шел|бежит|стоит|стоя[тл]|"
    r"смотр|меша|бега|ходит|кладет|кладёт|кладут|несёт|несет|держ|чита|"
    r"крич|пада|подн|опуск|брос|толк|тяне|обня|обним|переда|"
    r"протяж",
    re.IGNORECASE,
)
_COGNITIVE_RE = re.compile(
    r"узнаёт|узнает|понимает|думает|чувствует|знает|помнит|"
    r"решил что|осознал",
    re.IGNORECASE,
)
_TITLE_RE = re.compile(r"титр|имя|назван", re.IGNORECASE)


def visible_len(text: str) -> int:
    return len("".join(c for c in (text or "") if unicodedata.category(c) != "Mn"))


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


_PAD_STEPS = (
    "останавливается у стены",
    "поворачивается к двери",
    "смотрит на стол",
    "берёт бумагу",
    "идёт дальше по коридору",
    "стоит у окна",
    "кладёт руку на стол",
    "открывает дверь",
    "читает следующую строку",
    "проходит вдоль стены",
)


def _shot_count_for_vo(vo: str, n_steps: int) -> int:
    """Сколько кадров нужно, чтобы закадр влез в 13–80."""
    text = " ".join((vo or "").split())
    vis = visible_len(text)
    words = text.split()
    if not text:
        return max(1, n_steps)
    max_n = min(len(words), vis // SHOT_VO_MIN if vis >= SHOT_VO_MIN else 1)
    min_n = 1 if vis <= SHOT_VO_MAX else (vis + SHOT_VO_MAX - 1) // SHOT_VO_MAX
    return max(1, min(max(n_steps, min_n), max_n))


def _pad_unique_steps(steps: list[str], n: int) -> list[str]:
    out = list(steps)
    seen = {action_stem(s) for s in out if action_stem(s)}
    for extra in _PAD_STEPS:
        if len(out) >= n:
            break
        stem = action_stem(extra)
        if stem and stem not in seen and has_visible_verb(extra):
            out.append(extra)
            seen.add(stem)
    return out


def _split_vo_for_steps(vo: str, n: int) -> list[str]:
    """Режет закадр на n кусков 13–80. Шаги действия важнее границ клаузы."""
    text = " ".join((vo or "").split())
    if not text:
        return [""] * max(1, n)
    vis = visible_len(text)
    words = text.split()
    n = _shot_count_for_vo(text, n)
    if n <= 1:
        return [text]
    parts: list[str] = []
    i = 0
    for p in range(n):
        remaining_parts = n - p
        rest = words[i:]
        if remaining_parts <= 1 or not rest:
            parts.append(" ".join(rest))
            break
        rest_vis = visible_len(" ".join(rest))
        max_take = min(SHOT_VO_MAX, rest_vis - (remaining_parts - 1) * SHOT_VO_MIN)
        min_take = max(
            SHOT_VO_MIN, rest_vis - (remaining_parts - 1) * SHOT_VO_MAX
        )
        want = min(max(rest_vis / remaining_parts, min_take), max_take)
        chunk: list[str] = []
        size = 0
        while i < len(words):
            leftover_words = len(words) - i
            if chunk and leftover_words < remaining_parts:
                break
            add = visible_len(words[i]) + (1 if chunk else 0)
            nxt = size + add
            if chunk and nxt > max_take:
                break
            if chunk and size >= want:
                break
            chunk.append(words[i])
            size = nxt
            i += 1
        if not chunk and i < len(words):
            chunk = [words[i]]
            i += 1
        parts.append(" ".join(chunk) if chunk else "")
    return [p for p in parts if p] or [text]


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


def camera_pack(
    *,
    obj: str,
    place_new: bool,
    position: str,
    hod: bool,
    prev: dict[str, Any] | None,
) -> dict[str, Any]:
    key = obj if obj in _PACKS else "тело"
    pack = dict(_PACKS[key])
    if position == "вход" and place_new:
        pack["план"] = "ОБЩИЙ"
        pack["линза_мм"] = 24
        pack["точка"] = "фронт"
    if position == "пик" and obj == "тело":
        pack["план"] = "СРЕДНЕ-КРУПНЫЙ"
        pack["линза_мм"] = 50
    if position == "пик" and obj == "лицо":
        pack["движение"] = "наезд"
    if hod:
        pack["движение"] = "следование"
    if not place_new and obj == "место":
        pack["план"] = "СРЕДНИЙ"
        pack["линза_мм"] = 35
        pack["точка"] = "3/4"
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
    """Карточки сцен → кадры. Камера из таблицы."""
    chain = parse_scene_chain(action)
    if not chain:
        return []
    out: list[dict[str, Any]] = []
    last_place = (prev_place or "").strip().casefold()
    order = 0
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
        need = _shot_count_for_vo(vo, len(steps))
        steps = _pad_unique_steps(steps, need)
        pieces = _split_vo_for_steps(vo, len(steps))
        if len(pieces) > len(steps):
            steps = _pad_unique_steps(steps, len(pieces))
        if len(pieces) < len(steps):
            steps = steps[: len(pieces)] or steps[:1]
        n = min(len(steps), len(pieces)) or 1
        master_id = f"{cell_number}-S{int(scene['n'])}-K1"
        prev_shot: dict[str, Any] | None = None
        for i in range(n):
            step = steps[i]
            obj = classify_object(step)
            shot_new = place_new and i == 0
            pos = _position(i, n, obj, shot_new)
            pack = camera_pack(
                obj=obj,
                place_new=shot_new,
                position=pos,
                hod=_hod(step),
                prev=prev_shot,
            )
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
        if place:
            last_place = place.casefold()
    return out


def apply_grammar_to_ops(ops: list[Any], frames: list[dict[str, Any]]) -> None:
    """Дописать кадры из главное_действие / дозаполнить камеру."""
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
        action = str(
            fields.get("главное_действие")
            or fields.get("main_action")
            or fr.get("main_action")
            or fr.get("главное_действие")
            or ""
        ).strip()
        if not action:
            attrs = fr.get("attrs") if isinstance(fr.get("attrs"), dict) else {}
            action = str(
                attrs.get("главное_действие") or attrs.get("main_action") or ""
            ).strip()
        shots = fields.get("кадры")
        need_expand = not isinstance(shots, list) or len(shots) < 1
        if action and need_expand:
            num = int(fr.get("number") or 1)
            fields["кадры"] = expand_action_to_shots(
                action, cell_number=num, prev_place=prev_place
            )
            shots = fields["кадры"]
        if isinstance(shots, list) and shots:
            vo = ""
            if isinstance(fr, dict):
                vo = str(fr.get("voiceover_text") or fr.get("закадр") or "").strip()
                attrs = fr.get("attrs") if isinstance(fr.get("attrs"), dict) else {}
                if not vo and isinstance(attrs, dict):
                    vo = str(
                        attrs.get("закадр") or attrs.get("voiceover_text") or ""
                    ).strip()
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
        pos = str(shot.get("позиция") or "") or _position(i, n, obj, place_new)
        pack = camera_pack(
            obj=obj,
            place_new=place_new,
            position=pos,
            hod=_hod(step),
            prev=prev,
        )
        shot.setdefault("план", pack["план"])
        shot.setdefault("линза_мм", pack["линза_мм"])
        shot.setdefault("ракурс", pack["ракурс"])
        shot.setdefault("высота", pack["высота"])
        shot.setdefault("наклон", pack["наклон"])
        shot.setdefault("точка", pack["точка"])
        shot.setdefault("движение", pack["движение"])
        shot.setdefault("стык", pack["стык"])
        if place_new or i == 0:
            shot["parent_id"] = None
        elif not str(shot.get("parent_id") or "").strip():
            first = shots[0] if isinstance(shots[0], dict) else {}
            shot["parent_id"] = first.get("id")
        if place:
            last_place = place.casefold()
        prev = shot


def shots_grammar_reason(shots: list[Any], vo: str, uid: str = "") -> str | None:
    prefix = f"uuid {uid[:8]}: " if uid else ""
    if not shots:
        return f"{prefix}пустые кадры"
    if len(shots) == 1 and visible_len(vo) > SHOT_VO_MAX:
        return (
            f"{prefix}один кадр на закадр {visible_len(vo)} симв. "
            f"(больше {SHOT_VO_MAX} — нужен ещё шаг действия)"
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
        sid = str(shot.get("id") or "")
        chunk = str(shot.get("закадр") or "").strip()
        n = visible_len(chunk)
        if not chunk:
            return (
                f"{prefix}кадр {sid or '?'} пустой закадр "
                "— у каждого кадра свой кусок"
            )
        if n < SHOT_VO_MIN and not (
            len(shots) == 1 and visible_len(vo) <= SHOT_VO_MAX
        ):
            words = re.findall(r"[^\W\d_]+", chunk, flags=re.UNICODE)
            title = bool(_TITLE_RE.search(step))
            name_like = 1 <= len(words) <= 2 and all(
                w[:1].isupper() for w in words if w
            )
            if not (title or name_like):
                return (
                    f"{prefix}кадр закадр {n} симв. "
                    f"(нужно {SHOT_VO_MIN}–{SHOT_VO_MAX})"
                )
        if chunk and n > SHOT_VO_MAX:
            return (
                f"{prefix}кадр закадр {n} симв. "
                f"(нужно {SHOT_VO_MIN}–{SHOT_VO_MAX})"
            )
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
