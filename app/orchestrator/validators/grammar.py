"""Предикаты R-MERGE-TRIVIAL и R-WIDE-BEFORE-DIALOG.

TODO(R0a): после мёржа ``r0a-pipeline-base`` удалить локальные копии и оставить
вызов ``app.services.scene_shot_grammar.merge_trivial_shots`` /
``ensure_wide_before_dialogue``. Пока модуля нет, правила скопированы оттуда
(функции ниже помечены как временные). Валидатор всегда зовёт их на deepcopy
и наружу список кадров не меняет.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from app.orchestrator.validators.activity_flags import shot_is_active

_re = re

_SHOT_IS_ACTIVE: Any = _re  # sentinel: ещё не искали frame_tombstones


def _external_shot_is_active() -> Any:
    """``shot_is_active``, если модуль уже в дереве. Иначе None."""

    global _SHOT_IS_ACTIVE
    if _SHOT_IS_ACTIVE is not _re:
        return _SHOT_IS_ACTIVE
    try:
        from app.services.frame_tombstones import shot_is_active
    except ImportError:  # на #351 модуля нет; прочие ошибки не глотаем
        shot_is_active = None
    _SHOT_IS_ACTIVE = shot_is_active if callable(shot_is_active) else None
    return _SHOT_IS_ACTIVE


def shot_counts_as_active(shot: Any) -> bool:
    """Живой шот. И путь с frame_tombstones, и фолбэк зовут activity_flags.

    Булев ответ внешнего чекера не используется: матрица одна. Сам вызов
    остаётся, чтобы RuntimeError из импорта не глотался (ловится только ImportError).
    """

    checker = _external_shot_is_active()
    if checker is not None:
        return shot_is_active(shot)
    return shot_is_active(shot)


def active_shots(shots: list[Any] | None) -> list[Any] | None:
    """Оставить живые шоты. None — списка не было. Сами объекты не меняются."""

    if shots is None:
        return None
    return [shot for shot in shots if shot_counts_as_active(shot)]


# ---- временная копия предиката (r0a scene_shot_grammar, 2026-10-15) ----
_POV_OBJ = {"взгляд"}
_NOTICE_RE = _re.compile(
    r"замеча|замети|видит|увидел|смотрит на|разгляд|обнаруж|наход|нашёл|нашел|читает",
    _re.I,
)
MERGE_MAX_ACTION_CHARS = 240
_CONT_RE = _re.compile(
    r"оставаясь|продолжа|не вставая|не отпуская|всё ещё|все еще|затем|сидя |стоя |держа ",
    _re.I,
)
_KID_RE = _re.compile(r"^(.*-K)(\d+)$")
_PLAN_RX = _re.compile(r"План\s+(ДАЛЬНИЙ|ОБЩИЙ|СРЕДНЕ-КРУПНЫЙ|СРЕДНИЙ|КРУПНЫЙ|ДЕТАЛЬ)")
_ANG_RX = _re.compile(r"Ракурс\s+[^.]*\.")
_HGT_RX = _re.compile(r"Высота\s+(снизу вверх|сверху вниз|уровень глаз)")


def _shot_s(s: Any, k: str) -> str:
    return " ".join(str((s or {}).get(k) or "").split())


def _who_key(s: Any) -> str:
    t = _shot_s(s, "кто").lower().replace("ё", "е")
    ids = sorted(set(_re.findall(r"c\d{2}", t)))
    if ids:
        return ",".join(ids)
    return ",".join(sorted(x.strip() for x in _re.split(r"[,;]| и ", t) if x.strip()))


def _subject_key(s: Any) -> str:
    a = _shot_s(s, "действие").lower().replace("ё", "е")
    ids = _re.findall(r"c\d{2}", a[:80])
    if ids:
        return ids[0]
    w = _re.findall(r"[а-яa-z]+", a)
    return w[0] if w else ""


def _is_reply(s: Any) -> bool:
    return bool(_shot_s(s, "реплика")) or bool(_shot_s(s, "говорит"))


def _can_merge_trivial(a: Any, b: Any, nxt: Any) -> bool:
    if not (isinstance(a, dict) and isinstance(b, dict)):
        return False
    if _is_reply(a) or _is_reply(b):
        return False
    if nxt is not None and _is_reply(nxt):
        return False
    if _shot_s(b, "объект") in _POV_OBJ or not _shot_s(b, "кто"):
        return False
    if _NOTICE_RE.search(_shot_s(a, "действие")):
        return False
    if _shot_s(a, "закадр") and _shot_s(b, "закадр"):
        return False
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
    # Форма как в r0a scene_shot_grammar, чтобы diff предиката оставался узнаваемым.
    too_long = len(_shot_s(a, "действие")) + len(_shot_s(b, "действие")) > MERGE_MAX_ACTION_CHARS
    return not too_long


def _set_cam_text(s: dict[str, Any], plan: str, ang_sentence: str) -> None:
    for k in [k for k in s if str(k).startswith("промт") and k != "промт_видео"]:
        t = s.get(k)
        if not isinstance(t, str) or not t:
            continue
        t = _PLAN_RX.sub(f"План {plan}", t, count=1)
        t = _ANG_RX.sub(ang_sentence, t, count=1)
        t = _HGT_RX.sub("Высота уровень глаз", t, count=1)
        s[k] = t


def _merge_active_neighbors(
    shots: list[Any],
    reserved_numbers: set[int] | None = None,
) -> list[str]:
    """Склейка списка, в котором неактивных уже нет."""

    if not isinstance(shots, list) or len(shots) < 2:
        return []
    notes: list[str] = []
    out = [shots[0]]
    merged_into: dict[str, str] = {}
    chain = 1
    for i in range(1, len(shots)):
        a, b = out[-1], shots[i]
        nxt = shots[i + 1] if i + 1 < len(shots) else None
        if chain < 2 and _can_merge_trivial(a, b, nxt):
            act = _shot_s(a, "действие").rstrip(".")
            a["действие"] = (act + "; " + _shot_s(b, "действие")).strip()
            if _shot_s(b, "конец"):
                a["конец"] = b.get("конец")
            if _shot_s(b, "закадр") and not _shot_s(a, "закадр"):
                a["закадр"] = b.get("закадр")
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
    reserved = set(reserved_numbers or ())
    k = 0
    for s in out:
        if not isinstance(s, dict):
            continue
        k = _next_free_k(k + 1, reserved, set(), None)
        if "порядок" in s:
            s["порядок"] = k
        pid = str(s.get("parent_id") or "")
        if pid in merged_into:
            s["parent_id"] = merged_into[pid]
        reserved.add(k)
    shots[:] = out
    return notes


def local_merge_trivial_shots(shots: list[Any]) -> list[str]:
    """Временная копия ``merge_trivial_shots``. TODO(R0a): удалить.

    Неактивный шот не склеивается и не меняет ``порядок`` живых. Из списка
    он не выкидывается и его поля не пишутся заново.
    """

    if not isinstance(shots, list) or len(shots) < 2:
        return []
    if all(shot_counts_as_active(shot) for shot in shots):
        return _merge_active_neighbors(shots)
    original = list(shots)
    live = [shot for shot in original if shot_counts_as_active(shot)]
    reserved_numbers, _reserved_ids = _reserved_by_inactive(original)
    notes = _merge_active_neighbors(live, reserved_numbers)
    survived = {id(shot) for shot in live}
    shots[:] = [
        shot
        for shot in original
        if (not shot_counts_as_active(shot)) or id(shot) in survived
    ]
    return notes


def _reserved_by_inactive(shots: list[Any]) -> tuple[set[int], set[str]]:
    """Номера и id, которые неактивные шоты не отдают под перенумерацию."""

    numbers: set[int] = set()
    ids: set[str] = set()
    for shot in shots:
        if not isinstance(shot, dict) or shot_counts_as_active(shot):
            continue
        sid = str(shot.get("id") or "")
        if sid:
            ids.add(sid)
        order = shot.get("порядок")
        if isinstance(order, int) and not isinstance(order, bool):
            numbers.add(order)
        matched = _KID_RE.match(sid)
        if matched:
            numbers.add(int(matched.group(2)))
    return numbers, ids


def _next_free_k(
    start: int,
    numbers: set[int],
    ids: set[str],
    prefix: str | None,
) -> int:
    k = start
    while k in numbers or (prefix is not None and f"{prefix}{k}" in ids):
        k += 1
    return k


def _previous_active(shots: list[Any], index: int) -> Any | None:
    cursor = index - 1
    while cursor >= 0:
        shot = shots[cursor]
        if shot_counts_as_active(shot):
            return shot
        cursor -= 1
    return None


def local_ensure_wide_before_dialogue(shots: list[Any]) -> list[str]:
    """Временная копия ``ensure_wide_before_dialogue``. TODO(R0a): удалить.

    Говорящие и предыдущий план смотрятся только у живых шотов. Неактивный
    остаётся на своём месте, его id и ``порядок`` не переписываются.
    """

    if not isinstance(shots, list) or not shots:
        return []
    notes: list[str] = []
    i = 0
    while i < len(shots):
        s = shots[i]
        if not shot_counts_as_active(s):
            i += 1
            continue
        prev = _previous_active(shots, i)
        if not (isinstance(s, dict) and _is_reply(s)) or (
            isinstance(prev, dict) and _is_reply(prev)
        ):
            i += 1
            continue
        j = i
        speakers: set[str] = set()
        while j < len(shots):
            current = shots[j]
            if not shot_counts_as_active(current):
                j += 1
                continue
            if isinstance(current, dict) and _is_reply(current):
                speakers.add(_shot_s(current, "говорит").casefold())
                j += 1
                continue
            break
        if len(speakers) >= 2 and not (
            isinstance(prev, dict) and _shot_s(prev, "план").upper() == "ОБЩИЙ"
        ):
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
            shots.insert(i, w)
            notes.append(f"{s.get('id')}: вставлен ОБЩИЙ установочный перед диалогом")
            i = j + 1
            continue
        i = j
    if notes:
        idmap: dict[str, str] = {}
        reserved_numbers, reserved_ids = _reserved_by_inactive(shots)
        k = 0
        for s in shots:
            if not isinstance(s, dict) or not shot_counts_as_active(s):
                continue
            old = str(s.get("id") or "")
            matched = _KID_RE.match(old)
            prefix = matched.group(1) if matched else None
            k = _next_free_k(k + 1, reserved_numbers, reserved_ids, prefix)
            if matched and prefix is not None:
                new = f"{prefix}{k}"
                if old and old not in idmap:
                    idmap[old] = new
                s["id"] = new
                reserved_ids.add(new)
            if "порядок" in s:
                s["порядок"] = k
            reserved_numbers.add(k)
        for s in shots:
            if not isinstance(s, dict) or not shot_counts_as_active(s):
                continue
            if str(s.get("parent_id") or "") in idmap:
                s["parent_id"] = idmap[str(s.get("parent_id"))]
    return notes


def upstream_grammar() -> Any | None:
    """Модуль r0a, если он уже в дереве. Иначе None — работаем на локальной копии."""

    try:
        import app.services.scene_shot_grammar as mod
    except Exception:  # noqa: BLE001
        return None
    if not hasattr(mod, "merge_trivial_shots") or not hasattr(mod, "ensure_wide_before_dialogue"):
        return None
    return mod


def _apply(fn: Any, shots: Any) -> list[str]:
    if not isinstance(shots, list):
        return []
    clone = copy.deepcopy(shots)
    notes = fn(clone)
    return [str(item) for item in (notes or [])]


def merge_notes(shots: Any) -> list[str]:
    mod = upstream_grammar()
    fn = mod.merge_trivial_shots if mod is not None else local_merge_trivial_shots
    return _apply(fn, shots)


def wide_notes(shots: Any) -> list[str]:
    mod = upstream_grammar()
    fn = mod.ensure_wide_before_dialogue if mod is not None else local_ensure_wide_before_dialogue
    return _apply(fn, shots)


def shots_of(context: dict[str, Any]) -> list[Any] | None:
    """Кадры из context. None — проверять нечего.

    Неактивные (``активен is False``) в проверку не входят и из context не
    выкидываются: наружу уходит новый список.
    """

    if "shots" in context:
        value = context.get("shots")
        raw = value if isinstance(value, list) else []
    else:
        raw = None
        after = context.get("after")
        if isinstance(after, dict):
            for key in ("shots", "frames", "кадры"):
                if key in after and isinstance(after[key], list):
                    raw = after[key]
                    break
        if raw is None:
            for key in ("frames", "кадры"):
                if key in context and isinstance(context[key], list):
                    raw = context[key]
                    break
    return active_shots(raw)
