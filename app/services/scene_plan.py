"""План площадки сцены: зоны, проходы, двери, камера, люди — по компасу.

GPT (fw_action) пишет ``площадка`` на VO-ячейку, GPT (fw_shots) пишет в
каждом кадре ``зона`` / ``камера`` / ``люди`` / ``меняет``. Всё, что модель
не написала, код выводит из действия. Дальше код:

* ведёт состояния предметов (дверь закрыта, пока её не открыли в кадре);
* считает экран из направления камеры (смотрит на север → запад слева);
* чинит физику: проход через закрытую дверь (вставляет кадр на пороге),
  «к открытой двери» при закрытой, «идёт на месте», переворот направления
  движения на экране, переход оси 180° в паре;
* пишет в кадр ``раскладка``: для точного жеста (смена предмета) —
  старт до / конец после; для процесса — одно действие в разгаре,
  без пары «до/после». Положения предметов и отличие от референса.

Направления: север / восток / юг / запад / центр. Модель мира плоская,
без метрики: сторона зоны = где стоит предмет, человек или камера.
"""

from __future__ import annotations

import copy
import html
import re
from typing import Any

from app.services.scene_shot_grammar import (
    SHOT_VO_MIN,
    classify_object,
    visible_len,
)

DIRS = ("север", "восток", "юг", "запад")
CENTER = "центр"
_VEC = {
    "север": (0, 1),
    "восток": (1, 0),
    "юг": (0, -1),
    "запад": (-1, 0),
    CENTER: (0, 0),
}
_OPPOSITE = {"север": "юг", "юг": "север", "запад": "восток", "восток": "запад"}
_DIR_ALIASES = {
    "север": "север", "северн": "север", "n": "север", "north": "север",
    "верх": "север", "дальн": "север", "глубин": "север",
    "юг": "юг", "южн": "юг", "s": "юг", "south": "юг", "низ": "юг",
    "ближн": "юг",
    "запад": "запад", "западн": "запад", "w": "запад", "west": "запад",
    "лев": "запад",
    "восток": "восток", "восточн": "восток", "e": "восток", "east": "восток",
    "прав": "восток",
    "центр": CENTER, "середин": CENTER, "center": CENTER, "c": CENTER,
}
_FROM = {"север": "с севера", "юг": "с юга", "запад": "с запада", "восток": "с востока"}
_AT = {
    "север": "у северной стороны",
    "юг": "у южной стороны",
    "запад": "у западной стороны",
    "восток": "у восточной стороны",
    CENTER: "в центре",
}

OPEN = "открыта"
CLOSED = "закрыта"
ABSENT = "нет"

_PLACE_VERB_RE = re.compile(
    r"клад[её]т|кладет|ставит|оставляет|полож",
    re.IGNORECASE,
)
_ARRIVE_STATE_RE = re.compile(
    r"лежит|на столе|рядом|поставлена|положена",
    re.IGNORECASE,
)
_UNFOLD_RE = re.compile(r"разверн|разворач", re.IGNORECASE)
_ALREADY_CLAUSE_RE = re.compile(
    r"^.{0,80}?\bуже\b[^,.]*,\s*",
    re.IGNORECASE,
)

_DOOR_RE = re.compile(r"двер|калитк|ворот|люк", re.IGNORECASE)
_OPEN_VERB_RE = re.compile(
    r"открыва|открыл|распахива|распахнул|отворя|отворил|толка\w* двер",
    re.IGNORECASE,
)
_CLOSE_VERB_RE = re.compile(
    r"закрыва|закрыл|захлопыва|захлопнул|запира|запер",
    re.IGNORECASE,
)
_ENTER_RE = re.compile(
    r"вбега|забега|вход(?!н)|заход|вош[её]л|врыва|ворва|переступ|через двер|"
    r"в двер|порог|выход(?!н)|выбега|выш[её]л|заглядыва",
    re.IGNORECASE,
)
_MOVE_RE = re.compile(
    r"беж|бега|ид[её]т|ид[уё]|шага|шел|шёл|вбега|забега|подбега|выбега|"
    r"вход(?!н)|заход|подход|направля|спеш|мчит|крад|пробира|проход|выход(?!н)",
    re.IGNORECASE,
)
_ALREADY_IN_RE = re.compile(r"\bуже\s+(?:в|во|на)\s+[^,.;]+[,;]?\s*", re.IGNORECASE)
_OPEN_ADJ_RE = re.compile(
    r"(открыт|распахнут|приоткрыт)(ой|ую|ая|ые|ых|ым)(\s+\w+)?\s+(двер|калитк|ворот)",
    re.IGNORECASE,
)
_DOOR_OPEN_STATE_RE = re.compile(
    r"(двер\w*|калитк\w*|ворот\w*)\s+(уже\s+)?(открыт\w*|распахнут\w*|настежь)",
    re.IGNORECASE,
)
_OUTDOOR_RE = re.compile(
    r"улиц|двор|крыльц|подъезд|сад|площад|дорог|переул|снаружи", re.IGNORECASE
)
_SOFT_CUTS = ("dissolve", "fade", "затемн", "наплыв")
_ID_RE = re.compile(r"^(.*-S\d+)-K(\d+)$")


def _key(raw: Any) -> str:
    return " ".join(str(raw or "").casefold().replace("ё", "е").split())


def _stems(raw: Any) -> set[str]:
    words = re.findall(r"[^\W\d_]+", _key(raw))
    return {w[:4] for w in words if len(w) >= 4}


def _mentions(name: str, text: str) -> bool:
    """Все значимые слова имени есть в тексте (без окончаний)."""
    need = _stems(name)
    return bool(need) and need <= _stems(text)


def norm_dir(raw: Any) -> str | None:
    text = _key(raw)
    if not text:
        return None
    if text in _DIR_ALIASES:
        return _DIR_ALIASES[text]
    for stem, canon in _DIR_ALIASES.items():
        if len(stem) >= 3 and stem in text:
            return canon
    return None


def _right_of(look: str) -> tuple[int, int]:
    fx, fy = _VEC[look]
    return (fy, -fx)


def screen_of(look: str, where: str) -> str:
    """Где предмет на экране, если камера смотрит ``look``."""
    if where == CENTER or where not in _VEC or look not in _VEC:
        return "в центре"
    vx, vy = _VEC[where]
    fx, fy = _VEC[look]
    rx, ry = _right_of(look)
    if vx * fx + vy * fy > 0:
        return "в глубине"
    if vx * fx + vy * fy < 0:
        return "за камерой"
    return "справа" if vx * rx + vy * ry > 0 else "слева"


def lateral(look: str, where: str) -> int:
    """-1 слева, +1 справа, 0 по оси камеры."""
    if where not in _VEC or look not in _VEC:
        return 0
    vx, vy = _VEC[where]
    rx, ry = _right_of(look)
    return (vx * rx + vy * ry) or 0


def motion_on_screen(look: str, move: str) -> str:
    if move not in DIRS or look not in DIRS:
        return ""
    if move == look:
        return "от камеры в глубину"
    if move == _OPPOSITE[look]:
        return "на камеру"
    return "слева направо" if lateral(look, move) > 0 else "справа налево"


def _facing_on_screen(look: str, face: str) -> str:
    if face not in DIRS or look not in DIRS:
        return ""
    if face == look:
        return "спиной к камере"
    if face == _OPPOSITE[look]:
        return "лицом к камере"
    return "в профиль, смотрит вправо" if lateral(look, face) > 0 else (
        "в профиль, смотрит влево"
    )


def _step_toward(where: str, move: str) -> str:
    """Шаг по пути: противоположная сторона → центр → сторона движения."""
    if move not in DIRS:
        return where
    if where == _OPPOSITE[move]:
        return CENTER
    if where == CENTER or where not in _VEC:
        return move
    if where == move:
        return move
    return CENTER


def _base_place(raw: Any) -> str:
    text = str(raw or "").strip()
    for sep in (" — ", " - ", "(", ",", ":"):
        if sep in text:
            text = text.split(sep, 1)[0]
    return " ".join(text.split())


def _is_door(prop_id: str) -> bool:
    return bool(_DOOR_RE.search(prop_id or ""))


# ── План площадки ──────────────────────────────────────────────────────


def _as_list(raw: Any) -> list[Any]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        return [dict(v, id=k) if isinstance(v, dict) else {"id": k, "что": v}
                for k, v in raw.items()]
    return []


def _norm_prop(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, str):
        name = raw.strip()
        return {"id": name, "где": CENTER, "состояние": ""} if name else None
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("id") or raw.get("предмет") or raw.get("name") or "").strip()
    if not name:
        return None
    where = norm_dir(raw.get("где") or raw.get("where") or raw.get("сторона")) or CENTER
    state = str(raw.get("состояние") or raw.get("state") or "").strip()
    what = str(raw.get("что") or raw.get("описание") or raw.get("детали") or "").strip()
    out = {"id": name, "где": where, "состояние": _norm_state(state)}
    if what:
        out["что"] = what
    return out


def _norm_state(raw: str) -> str:
    text = _key(raw)
    if not text:
        return ""
    if text.startswith(("откр", "распах", "настеж", "приоткр", "open")):
        return OPEN
    if text.startswith(("закр", "запер", "захлоп", "closed", "close")):
        return CLOSED
    return str(raw).strip()


class ScenePlan:
    """Нормализованная площадка ячейки: зоны, проходы, люди, состояния."""

    def __init__(self) -> None:
        self.zones: dict[str, dict[str, Any]] = {}
        self.passages: list[dict[str, str]] = []
        self.people: dict[str, dict[str, str]] = {}
        self.derived: list[str] = []

    # зоны
    def zone_key(self, raw: Any) -> str | None:
        k = _key(raw)
        if not k:
            return None
        if k in self.zones:
            return k
        for zk in self.zones:
            if zk in k or k in zk:
                return zk
        for zk in self.zones:
            if _mentions(zk, k) or _mentions(k, zk):
                return zk
        return None

    def add_zone(self, name: str, what: str = "") -> str:
        k = _key(name)
        if k and k not in self.zones:
            self.zones[k] = {"id": name.strip(), "что": what, "предметы": {}}
        return k

    def props(self, zone: str) -> dict[str, dict[str, Any]]:
        return self.zones.get(zone, {}).get("предметы", {})

    def find_prop(self, zone: str | None, raw: Any) -> str | None:
        k = _key(raw)
        if not k:
            return None
        pools = [self.props(zone)] if zone else []
        pools += [z["предметы"] for z in self.zones.values()]
        for pool in pools:
            if k in pool:
                return k
        for pool in pools:
            for pk in pool:
                if _stems(pk) and _stems(pk) == _stems(k):
                    return pk
        for pool in pools:
            for pk in pool:
                if _mentions(pk, k):
                    return pk
        return None

    # проходы
    def passage(self, a: str, b: str) -> dict[str, str] | None:
        for p in self.passages:
            if {p["из"], p["в"]} == {a, b}:
                return p
        return None

    def door_side(self, zone: str, door: str) -> str:
        prop = self.props(zone).get(door)
        if prop and prop.get("где") in DIRS:
            return prop["где"]
        return "север"

    def initial_states(self) -> dict[str, str]:
        states: dict[str, str] = {}
        for zone in self.zones.values():
            for pk, prop in zone["предметы"].items():
                st = prop.get("состояние") or ""
                if pk not in states or (st and not states[pk]):
                    states[pk] = st
        for p in self.passages:
            door = p.get("через") or ""
            if door and not states.get(door):
                states[door] = CLOSED
        return states

    def to_dict(self) -> dict[str, Any]:
        return {
            "зоны": [
                {
                    "id": z["id"],
                    "что": z.get("что") or "",
                    "предметы": [
                        {
                            "id": p["id"],
                            "где": p["где"],
                            "состояние": p["состояние"],
                            **({"что": p["что"]} if p.get("что") else {}),
                        }
                        for p in z["предметы"].values()
                    ],
                }
                for z in self.zones.values()
            ],
            "проходы": [
                {
                    "из": self.zones[p["из"]]["id"],
                    "в": self.zones[p["в"]]["id"],
                    "через": self._prop_name(p.get("через") or ""),
                }
                for p in self.passages
                if p["из"] in self.zones and p["в"] in self.zones
            ],
            "люди": [
                {"кто": name, "зона": self.zones.get(v["зона"], {}).get("id", v["зона"]),
                 "где": v.get("где") or CENTER}
                for name, v in self.people.items()
            ],
        }

    def _prop_name(self, pk: str) -> str:
        for zone in self.zones.values():
            if pk in zone["предметы"]:
                return zone["предметы"][pk]["id"]
        return pk


def parse_plan(raw: Any) -> ScenePlan:
    plan = ScenePlan()
    if isinstance(raw, str):
        import json

        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = {}
    if not isinstance(raw, dict):
        return plan
    for z in _as_list(raw.get("зоны") or raw.get("zones")):
        if isinstance(z, str):
            plan.add_zone(z)
            continue
        if not isinstance(z, dict):
            continue
        name = str(z.get("id") or z.get("зона") or z.get("name") or "").strip()
        if not name:
            continue
        zk = plan.add_zone(name, str(z.get("что") or z.get("описание") or "").strip())
        for rp in _as_list(z.get("предметы") or z.get("props")):
            prop = _norm_prop(rp)
            if prop:
                plan.zones[zk]["предметы"][_key(prop["id"])] = prop
    for rp in _as_list(raw.get("проходы") or raw.get("passages")):
        if not isinstance(rp, dict):
            continue
        a = plan.zone_key(rp.get("из") or rp.get("from")) or (
            plan.add_zone(str(rp.get("из") or "")) if rp.get("из") else None
        )
        b = plan.zone_key(rp.get("в") or rp.get("to")) or (
            plan.add_zone(str(rp.get("в") or "")) if rp.get("в") else None
        )
        if not a or not b or a == b:
            continue
        door_name = str(rp.get("через") or rp.get("via") or "").strip()
        _add_passage(plan, a, b, door_name, state=_norm_state(str(rp.get("состояние") or "")))
    for rp in _as_list(raw.get("люди") or raw.get("people")):
        if not isinstance(rp, dict):
            continue
        who = str(rp.get("кто") or rp.get("id") or rp.get("name") or "").strip()
        if not who:
            continue
        zk = plan.zone_key(rp.get("зона")) or ""
        plan.people[who] = {"зона": zk, "где": norm_dir(rp.get("где")) or CENTER}
    return plan


def _add_passage(
    plan: ScenePlan, a: str, b: str, door_name: str, *, state: str = ""
) -> dict[str, str]:
    existing = plan.passage(a, b)
    if existing:
        return existing
    if not door_name:
        outdoor = _OUTDOOR_RE.search(plan.zones[a]["id"]) or _OUTDOOR_RE.search(
            plan.zones[b]["id"]
        )
        door_name = "входная дверь" if outdoor and not plan.find_prop(
            None, "входная дверь"
        ) else f"дверь «{plan.zones[b]['id']}»"
    dk = plan.find_prop(None, door_name) or _key(door_name)
    name = plan._prop_name(dk) if dk != _key(door_name) else door_name
    for zk, side in ((a, "север"), (b, "юг")):
        pool = plan.zones[zk]["предметы"]
        if dk not in pool:
            pool[dk] = {"id": name, "где": side, "состояние": ""}
        if state and not pool[dk].get("состояние"):
            pool[dk]["состояние"] = state
    p = {"из": a, "в": b, "через": dk}
    plan.passages.append(p)
    return p


# ── Кадры ──────────────────────────────────────────────────────────────


def _shot_text(shot: dict[str, Any]) -> str:
    return str(shot.get("действие") or shot.get("action") or "").strip()


def _norm_camera(raw: Any) -> dict[str, str]:
    if isinstance(raw, str):
        raw = {"смотрит": raw}
    if not isinstance(raw, dict):
        return {}
    where = norm_dir(raw.get("где") or raw.get("from") or raw.get("стоит"))
    look = norm_dir(raw.get("смотрит") or raw.get("look") or raw.get("на"))
    if where == CENTER:
        where = None
    if look == CENTER:
        look = None
    if where and not look:
        look = _OPPOSITE[where]
    if look and not where:
        where = _OPPOSITE[look]
    if not where or not look:
        return {}
    return {"где": where, "смотрит": look}


def _norm_people(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in _as_list(raw) if not isinstance(raw, str) else []:
        if isinstance(item, str):
            item = {"кто": item}
        if not isinstance(item, dict):
            continue
        who = str(item.get("кто") or item.get("id") or item.get("name") or "").strip()
        if not who:
            continue
        row = {"кто": who}
        where = norm_dir(item.get("где") or item.get("where"))
        if where:
            row["где"] = where
        face = norm_dir(item.get("лицом") or item.get("facing"))
        if face and face != CENTER:
            row["лицом"] = face
        move = norm_dir(item.get("движется") or item.get("moves"))
        if move and move != CENTER:
            row["движется"] = move
        out.append(row)
    return out


def _names_from_shot(shot: dict[str, Any]) -> list[str]:
    raw = shot.get("кто") or shot.get("персонажи") or shot.get("characters") or ""
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [p.strip() for p in re.split(r"[,;/]| и ", str(raw)) if p.strip()]


def _norm_changes(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for item in _as_list(raw) if not isinstance(raw, str) else []:
        if not isinstance(item, dict):
            continue
        prop = str(item.get("предмет") or item.get("id") or "").strip()
        state = _norm_state(str(item.get("состояние") or item.get("state") or ""))
        if prop and state:
            out.append({"предмет": prop, "состояние": state})
    return out


def _cut_is_soft(shot: dict[str, Any]) -> bool:
    cut = _key(shot.get("стык"))
    return any(s in cut for s in _SOFT_CUTS)


def _issue(shot: dict[str, Any], kind: str, text: str, *, fixed: bool) -> dict[str, Any]:
    return {"_shot": shot, "вид": kind, "текст": text, "исправлено": fixed}


_PUNCT_END = (",", ".", ";", ":", "—", "!", "?", "…")


def _split_vo(text: str) -> tuple[str, str] | None:
    """Кусок закадра надвое: по знаку препинания ближе к середине, иначе по слову."""
    words = str(text or "").split()
    if len(words) < 2:
        return None
    total = visible_len(" ".join(words))
    best: tuple[int, int, int] | None = None
    for cut in range(1, len(words)):
        a = " ".join(words[:cut])
        b = " ".join(words[cut:])
        if visible_len(a) < SHOT_VO_MIN or visible_len(b) < SHOT_VO_MIN:
            continue
        rank = 0 if words[cut - 1].endswith(_PUNCT_END) else 1
        score = abs(visible_len(a) - total // 2)
        if best is None or (rank, score) < best[:2]:
            best = (rank, score, cut)
    if best is None:
        return None
    cut = best[2]
    return " ".join(words[:cut]), " ".join(words[cut:])


def accusative(name: str) -> str:
    """«входная дверь» → «входную дверь», «калитка» → «калитку» (вин. падеж)."""
    out: list[str] = []
    for word in name.split():
        low = word.casefold()
        if low.endswith("ая"):
            word = word[:-2] + "ую"
        elif low.endswith("яя"):
            word = word[:-2] + "юю"
        elif low.endswith("ка") and low in {"калитка", "решётка", "решетка", "форточка"}:
            word = word[:-1] + "у"
        out.append(word)
    return " ".join(out)


def _renumber_scene(shots: list[dict[str, Any]], prefix: str) -> None:
    """После вставки: K-номера сцены ``prefix`` по порядку, parent_id = K1."""
    k = 0
    first = f"{prefix}-K1"
    for shot in shots:
        m = _ID_RE.match(str(shot.get("id") or ""))
        if not m or m.group(1) != prefix:
            continue
        k += 1
        shot["id"] = f"{prefix}-K{k}"
        if k > 1:
            shot["parent_id"] = first


def _replace_open_adj(text: str) -> str:
    def adj(m: re.Match[str]) -> str:
        return f"закрыт{m.group(2)}{m.group(3) or ''} {m.group(4)}"

    def state(m: re.Match[str]) -> str:
        return f"{m.group(1)} закрыта"

    return _DOOR_OPEN_STATE_RE.sub(state, _OPEN_ADJ_RE.sub(adj, text))


def _mentions_open_door(text: str) -> bool:
    return bool(_OPEN_ADJ_RE.search(text) or _DOOR_OPEN_STATE_RE.search(text))


def _is_close(shot: dict[str, Any]) -> bool:
    size = _key(shot.get("план"))
    return size.startswith(("круп", "детал", "cu", "ecu"))


_PLAN_RUNG = (
    ("дальний", 0, "ДАЛЬНИЙ"),
    ("общий", 1, "ОБЩИЙ"),
    ("средн", 2, "СРЕДНИЙ"),
    ("круп", 3, "КРУПНЫЙ"),
    ("детал", 4, "ДЕТАЛЬ"),
)


def _plan_rung(raw: Any) -> int | None:
    k = _key(raw)
    for prefix, n, _name in _PLAN_RUNG:
        if k.startswith(prefix):
            return n
    return None


def _plan_name(rung: int) -> str:
    names = ("ДАЛЬНИЙ", "ОБЩИЙ", "СРЕДНИЙ", "КРУПНЫЙ", "ДЕТАЛЬ")
    return names[max(0, min(4, int(rung)))]


def _step_away_plan(plan: str, obj: str) -> str:
    idx = _plan_rung(plan)
    if idx is None:
        idx = 2
    want = {"лицо": 3, "предмет": 4, "взгляд": 3, "место": 1}.get(_key(obj))
    if want is not None and want != idx:
        return _plan_name(want)
    nxt = idx + 1 if idx < 4 else idx - 1
    return _plan_name(nxt)


# Соседние имена ≥45° номинал — запас к правилу 30°. «с плеча» рядом с 3/4 не ставим.
_NAMED_ANGLE_30 = ("фронт", "3/4", "сверху", "снизу")
_ANGLE_TOKENS = ("с плеча", "макро", "сверху", "снизу", "3/4", "фронт")


def named_angle_token(raw: Any) -> str:
    """Короткое имя ракурса из поля (в т.ч. «уровень глаз, …, фронт»)."""
    k = _key(raw)
    if not k:
        return ""
    for name in _ANGLE_TOKENS:
        if _key(name) in k:
            return name
    return ""


def angles_under_30(prev: Any, cur: Any) -> bool:
    """True = именованные ракурсы ближе 30° (совпали или 3/4↔с плеча)."""
    a = named_angle_token(prev)
    b = named_angle_token(cur)
    if not a and not b:
        return True
    if a and b and a == b:
        return True
    return {a, b} == {"3/4", "с плеча"}


def next_named_angle_30(raw: Any, obj: Any = "") -> str:
    """Следующий именованный ракурс с шагом ≥30°."""
    cur = named_angle_token(raw) or _key(raw)
    objk = _key(obj)
    if objk in {"предмет", "взгляд"}:
        return "3/4" if cur in {"сверху", "макро"} else "сверху"
    order = list(_NAMED_ANGLE_30)
    if cur in order:
        return order[(order.index(cur) + 1) % len(order)]
    if cur == "с плеча":
        return "фронт"
    return "3/4"


def _with_angle_token(raw: Any, nxt: str) -> str:
    text = str(raw or "").strip()
    tok = named_angle_token(text)
    if tok and tok in text:
        return text.replace(tok, nxt)
    return nxt


def _core_action(shot: dict[str, Any]) -> str:
    """Действие без префикса «уже лежит…,» — это конец прошлого кадра, не жест."""
    act = " ".join(str(shot.get("действие") or shot.get("action") or "").split())
    stripped = _ALREADY_CLAUSE_RE.sub("", act, count=1).strip()
    return stripped or act


def _actor_name(shot: dict[str, Any]) -> str:
    for row in shot.get("люди") or []:
        if isinstance(row, dict) and str(row.get("кто") or "").strip():
            return str(row["кто"]).strip()
    return ""


def _is_arrival(shot: dict[str, Any], state: str) -> bool:
    return bool(_PLACE_VERB_RE.search(_core_action(shot))) and bool(
        _ARRIVE_STATE_RE.search(state or "")
    )


def _freeze_copies_action(text: str, act: str) -> bool:
    if not text or not act:
        return False
    return _key(act) in _key(text)


_PRECISE_VERB_RE = re.compile(
    r"открыл|открыва|закрыл|закрыва|положил|клад[её]т|достал|вынул|"
    r"взял|бер[её]т|вставл|сунул|сорвал|разорв|подписал|поставил печат|"
    r"раскрыл|раскрыва",
    re.IGNORECASE,
)
_CONTENT_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"фотограф\w*|фото\b", "фотография"),
    (r"протокол\w*", "протокол"),
    (r"жалоб\w*", "жалоба"),
    (r"признан\w*", "признание"),
    (r"алиби", "лист алиби"),
    (r"отчёт\w*|отчет\w*", "отчёт"),
    (r"штамп\w*", "штамп"),
    (r"печат\w*", "печать"),
    (r"портрет\w*", "портрет"),
    (r"надпис\w*", "надпись на обложке"),
)


def _named_contents(shot: dict[str, Any]) -> str:
    blob = " ".join(
        str(shot.get(k) or "")
        for k in ("действие", "закадр", "конец", "старт", "объект")
    )
    hits: list[str] = []
    for rx, label in _CONTENT_PATTERNS:
        if re.search(rx, blob, re.IGNORECASE) and label not in hits:
            hits.append(label)
    return ", ".join(hits)


def _precision_flag(raw: Any) -> bool | None:
    if raw is True:
        return True
    if raw is False:
        return False
    s = str(raw or "").strip().casefold()
    if s in {"да", "1", "true", "точность"}:
        return True
    if s in {"нет", "0", "false", "процесс"}:
        return False
    return None


def _is_generic_freeze(start: str, end: str) -> bool:
    blob = f"{start} {end}".casefold()
    return any(
        bit in blob
        for bit in (
            "жест завершён",
            "жест завершен",
            "позе до жеста",
            "действие ещё не началось",
            "ещё до жеста —",
        )
    )


def needs_freeze(shot: dict[str, Any]) -> bool:
    """Пара старт/конец только у точного жеста (смена предмета), не у процесса."""
    flag = _precision_flag(shot.get("точность"))
    if flag is False:
        return False
    for c in shot.get("меняет") or []:
        if (
            isinstance(c, dict)
            and str(c.get("предмет") or "").strip()
            and str(c.get("состояние") or "").strip()
        ):
            return True
    if flag is True:
        return True
    start = " ".join(str(shot.get("старт") or "").split())
    end = " ".join(str(shot.get("конец") or "").split())
    if _is_generic_freeze(start, end):
        return False
    act = _core_action(shot)
    if start and end and not _freeze_broken(start, end, act):
        return True
    return bool(_PRECISE_VERB_RE.search(act))


def _freeze_broken(start: str, end: str, act: str) -> bool:
    """Старт = конец или оба копируют глагол — в кадре нет видимого изменения."""
    if not start or not end:
        return True
    if _key(start) == _key(end):
        return True
    if _freeze_copies_action(start, act) and _freeze_copies_action(end, act):
        return True
    if start.startswith("ещё до жеста —") and end.startswith("жест завершён —"):
        rest_s = start.split("—", 1)[-1].strip()
        rest_e = end.split("—", 1)[-1].strip()
        if _key(rest_s) == _key(rest_e) or _key(rest_s) == _key(act):
            return True
    return False


def _freeze_from_changes(shot: dict[str, Any]) -> tuple[str, str]:
    """Состояние до жеста / после жеста. Не копировать глагол действия."""
    who = _actor_name(shot) or "герой"
    act = _core_action(shot)
    start_bits: list[str] = []
    end_bits: list[str] = []
    contents = _named_contents(shot)
    for c in shot.get("меняет") or []:
        if not isinstance(c, dict):
            continue
        prop = str(c.get("предмет") or "").strip()
        st = str(c.get("состояние") or "").strip()
        if not prop or not st:
            continue
        if _is_arrival(shot, st):
            start_bits.append(
                f"стола без «{prop}», {who} ещё держит её в руках"
            )
            end_bits.append(f"«{prop}» уже {st}, руки на ней")
        elif st == OPEN or st in {"раскрыта", "раскрыт"}:
            start_bits.append(
                f"«{prop}» ещё закрыта, руки ещё не трогают"
            )
            if contents:
                end_bits.append(
                    f"«{prop}» {st}, на развороте видно: {contents}"
                )
            else:
                end_bits.append(
                    f"«{prop}» {st}, на развороте заполненные листы дела "
                    f"(заголовки, пометки, штампы — не пустая бумага)"
                )
        elif "развёрнут" in st or _UNFOLD_RE.search(st) or _UNFOLD_RE.search(act):
            start_bits.append(f"«{prop}» ещё не развёрнут")
            end_bits.append(f"«{prop}» {st}")
        else:
            start_bits.append(f"«{prop}» ещё не {st}")
            end_bits.append(f"«{prop}» {st}")
    if not start_bits:
        start_bits.append(f"ещё до «{act}»: {who} не начал это действие")
        if contents:
            end_bits.append(f"уже сделано: {act}; видно: {contents}")
        else:
            end_bits.append(f"уже сделано: {act}")
    return ", ".join(start_bits), ", ".join(end_bits)


def _freeze_pair(shot: dict[str, Any]) -> tuple[str, str]:
    start = " ".join(str(shot.get("старт") or "").split())
    end = " ".join(str(shot.get("конец") or "").split())
    act = _core_action(shot)
    if not _freeze_broken(start, end, act):
        return start, end
    return _freeze_from_changes(shot)


def _cam_brief(shot: dict[str, Any]) -> str:
    cam = shot.get("камера") if isinstance(shot.get("камера"), dict) else {}
    plan = str(shot.get("план") or "").strip()
    ang = str(shot.get("ракурс") or "").strip() or "не задан"
    where = str(cam.get("где") or "")
    look = str(cam.get("смотрит") or "")
    bits: list[str] = []
    if plan:
        bits.append(f"план {plan}")
    if where in _FROM and look:
        bits.append(f"камера {_FROM[where]}, смотрит на {look}")
    bits.append(f"ракурс {ang}")
    return ", ".join(bits)


def _people_at_end(shot: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in shot.get("люди") or []:
        if not isinstance(row, dict):
            continue
        r = dict(row)
        move = r.get("движется")
        if move in DIRS:
            r["где"] = _step_toward(r.get("где", CENTER), move)
            r["_arrived"] = True
        out.append(r)
    return out


def _prop_label(
    prop: dict[str, Any],
    start: dict[str, str],
    pk: str,
    *,
    zone_what: str = "",
) -> str:
    state = start.get(pk) or prop.get("состояние") or ""
    name = str(prop.get("id") or pk)
    what = str(prop.get("что") or "").strip()
    if not what and zone_what and str(prop.get("где") or "") == CENTER:
        what = zone_what
    label = name
    if what and _key(what) != _key(name):
        label = f"{name} — {what}"
    if state:
        label += f" ({state})"
    return label


def _focal_prop_keys(shot: dict[str, Any], props: dict[str, dict[str, Any]]) -> set[str]:
    blob = _key(
        " ".join(
            [
                str(shot.get("действие") or ""),
                " ".join(
                    str(c.get("предмет") or "")
                    for c in (shot.get("меняет") or [])
                    if isinstance(c, dict)
                ),
            ]
        )
    )
    blob_stems = _stems(blob)
    found: set[str] = set()
    for pk, prop in props.items():
        name = str(prop.get("id") or pk)
        words = [w for w in re.findall(r"[^\W\d_]+", _key(name)) if len(w) >= 4]
        primary = words[0][:4] if words else ""
        extra = {s for s in _stems(prop.get("что") or "") if len(s) >= 4}
        if primary and primary in blob_stems:
            found.add(pk)
            continue
        stems = {w[:4] for w in words} | extra
        if len(stems) >= 2 and len(stems & blob_stems) >= 2:
            found.add(pk)
    return found


def _turn(cam: dict[str, str], side: str) -> dict[str, str]:
    """Камера на соседнюю сторону (90°): ``side`` = новая сторона стояния."""
    return {"где": side, "смотрит": _OPPOSITE[side]}


class _Sim:
    """Прогон кадров подряд. ``_ro`` — кадр соседней ячейки: только контекст."""

    def __init__(self, plan: ScenePlan, shots: list[dict[str, Any]]) -> None:
        self.plan = plan
        self.shots = shots
        self.issues: list[dict[str, Any]] = []

    @staticmethod
    def owned(shot: dict[str, Any]) -> bool:
        return not shot.get("_ro")

    # 1. зоны кадров и проходы
    def assign_zones(self) -> None:
        prev_zone: str | None = None
        for shot in self.shots:
            raw = shot.get("зона") or ""
            zk = self.plan.zone_key(raw) if raw else None
            if not zk:
                base = _base_place(shot.get("место") or shot.get("place"))
                zk = self.plan.zone_key(base) if base else None
                if not zk and base:
                    zk = self.plan.add_zone(base)
                    self.plan.derived.append(f"зона «{base}» из места кадра")
                if not zk and raw:
                    zk = self.plan.add_zone(str(raw))
            if not zk:
                zk = prev_zone or self.plan.add_zone("площадка")
            shot["зона"] = self.plan.zones[zk]["id"]
            shot["_zk"] = zk
            prev_zone = zk

    def derive_passages(self) -> None:
        for prev, cur in zip(self.shots, self.shots[1:], strict=False):
            a, b = prev["_zk"], cur["_zk"]
            if a == b or self.plan.passage(a, b):
                continue
            if _cut_is_soft(cur):
                continue
            text = f"{_shot_text(prev)} {_shot_text(cur)}"
            if not _ENTER_RE.search(text) and not _DOOR_RE.search(text):
                continue
            door = ""
            for zk in (a, b):
                for pk, prop in self.plan.props(zk).items():
                    if _is_door(pk) and _mentions(pk, text):
                        door = prop["id"]
                        break
                if door:
                    break
            _add_passage(self.plan, a, b, door)
            self.plan.derived.append(
                f"проход «{self.plan.zones[a]['id']}» → «{self.plan.zones[b]['id']}»"
            )

    # 2. камера и люди
    def entry_door(self, i: int) -> tuple[str, str] | None:
        """(дверь, сторона в текущей зоне), если в зону вошли из прошлого кадра."""
        if i == 0:
            return None
        a, b = self.shots[i - 1]["_zk"], self.shots[i]["_zk"]
        if a == b:
            return None
        p = self.plan.passage(a, b)
        if not p:
            return None
        door = p["через"]
        return door, self.plan.door_side(b, door)

    def exit_door(self, i: int) -> tuple[str, str] | None:
        if i + 1 >= len(self.shots):
            return None
        a, b = self.shots[i]["_zk"], self.shots[i + 1]["_zk"]
        if a == b:
            return None
        p = self.plan.passage(a, b)
        if not p:
            return None
        door = p["через"]
        return door, self.plan.door_side(a, door)

    def assign_cameras(self) -> None:
        last_cam: dict[str, dict[str, str]] = {}
        for i, shot in enumerate(self.shots):
            cam = _norm_camera(shot.get("камера"))
            if cam:
                shot["_cam_given"] = True
            else:
                zk = shot["_zk"]
                entry = self.entry_door(i)
                if entry:
                    side = entry[1]
                    cam = {"где": _OPPOSITE.get(side, "север"), "смотрит": side}
                elif zk in last_cam:
                    cam = dict(last_cam[zk])
                else:
                    cam = {"где": "юг", "смотрит": "север"}
            shot["камера"] = cam
            last_cam[shot["_zk"]] = cam

    def assign_people(self) -> None:
        last: dict[str, dict[str, str]] = {}
        known = list(self.plan.people)
        for i, shot in enumerate(self.shots):
            zk = shot["_zk"]
            text = _shot_text(shot)
            people = _norm_people(shot.get("люди") or shot.get("кто_где"))
            if not people:
                names = _names_from_shot(shot) or [
                    n for n in known if self.plan.people[n].get("зона") == zk
                ][:2]
                if not names and _MOVE_RE.search(text):
                    names = ["герой"]
                people = [{"кто": n} for n in names]
            moving = bool(_MOVE_RE.search(text))
            entry = self.entry_door(i)
            exit_ = self.exit_door(i)
            for row in people:
                prev = last.get(row["кто"])
                same_zone = prev is not None and prev.get("_zk") == zk
                if "движется" not in row and moving and len(people) == 1:
                    if exit_:
                        move = exit_[1]
                    elif entry:
                        move = _OPPOSITE.get(entry[1], "")
                    elif same_zone and prev.get("движется"):
                        move = prev["движется"]
                    else:
                        move = shot["камера"]["смотрит"]
                    if move in DIRS:
                        row["движется"] = move
                if "где" not in row:
                    move = row.get("движется", "")
                    if same_zone:
                        pm = prev.get("движется", "")
                        row["где"] = _step_toward(prev.get("где", CENTER), pm) if pm else (
                            prev.get("где", CENTER)
                        )
                    elif entry:
                        row["где"] = entry[1]
                    elif self.plan.people.get(row["кто"], {}).get("зона") == zk:
                        row["где"] = self.plan.people[row["кто"]].get("где") or CENTER
                    elif move in DIRS:
                        row["где"] = _OPPOSITE[move]
                    else:
                        row["где"] = CENTER
                if "лицом" not in row and row.get("движется"):
                    row["лицом"] = row["движется"]
                last[row["кто"]] = {**row, "_zk": zk}
            shot["люди"] = people

    # 3. состояния предметов
    def infer_changes(self) -> None:
        for i, shot in enumerate(self.shots):
            changes = _norm_changes(shot.get("меняет"))
            text = _shot_text(shot)
            touched = {self.plan.find_prop(shot["_zk"], c["предмет"]) for c in changes}
            for verb_re, state in ((_OPEN_VERB_RE, OPEN), (_CLOSE_VERB_RE, CLOSED)):
                if not verb_re.search(text) or not _DOOR_RE.search(text + " дверь"):
                    continue
                door = self._door_for_text(i, text)
                if door and door not in touched:
                    changes.append(
                        {"предмет": self.plan._prop_name(door), "состояние": state}
                    )
                    touched.add(door)
            shot["меняет"] = changes

    def _door_for_text(self, i: int, text: str) -> str | None:
        zk = self.shots[i]["_zk"]
        for pk in self.plan.props(zk):
            if _is_door(pk) and _mentions(pk, text):
                return pk
        exit_ = self.exit_door(i)
        if exit_:
            return exit_[0]
        doors = [pk for pk in self.plan.props(zk) if _is_door(pk)]
        return doors[0] if len(doors) == 1 else None

    def _apply_changes(self, states: dict[str, str], shot: dict[str, Any]) -> None:
        for c in shot.get("меняет") or []:
            pk = self.plan.find_prop(shot["_zk"], c["предмет"])
            if pk:
                states[pk] = c["состояние"]

    # 4. физика
    def check_doors(self) -> None:
        states = self.plan.initial_states()
        i = 0
        guard = 0
        while i < len(self.shots) and guard < 4 * len(self.shots) + 8:
            guard += 1
            shot = self.shots[i]
            shot["_start"] = dict(states)
            text = _shot_text(shot)
            zk = shot["_zk"]
            opens_here = {
                self.plan.find_prop(zk, c["предмет"])
                for c in shot.get("меняет") or []
                if c["состояние"] == OPEN
            }
            if _mentions_open_door(text) and self.owned(shot):
                door = self._door_for_text(i, text)
                if door and states.get(door) == CLOSED and door not in opens_here:
                    shot["действие"] = _replace_open_adj(text)
                    self.issues.append(_issue(
                        shot, "дверь",
                        f"«{text[:60]}»: дверь ещё закрыта — в тексте «закрытой»",
                        fixed=True,
                    ))
            entry = self.entry_door(i)
            if entry and states.get(entry[0]) == CLOSED:
                door_name = self.plan._prop_name(entry[0])
                prev = self.shots[i - 1]
                if not self.owned(prev):
                    self.issues.append(_issue(
                        shot, "порог",
                        f"вход через закрытую «{door_name}»: дверь не открыта "
                        "в кадре соседней ячейки",
                        fixed=False,
                    ))
                elif self._insert_threshold(i, entry[0]):
                    self.issues.append(_issue(
                        self.shots[i], "порог",
                        f"проход через закрытую «{door_name}» — вставлен кадр "
                        "«открывает дверь» снаружи",
                        fixed=True,
                    ))
                    continue
                else:
                    prev.setdefault("меняет", []).append(
                        {"предмет": door_name, "состояние": OPEN}
                    )
                    if not _OPEN_VERB_RE.search(_shot_text(prev)):
                        prev["действие"] = (
                            f"{_shot_text(prev)} и открывает {accusative(door_name)}"
                        ).strip()
                    self.issues.append(_issue(
                        prev, "порог",
                        f"проход через закрытую «{door_name}» — открывает в прошлом кадре",
                        fixed=True,
                    ))
                    i -= 1
                    states = dict(prev["_start"])
                    continue
            self._apply_changes(states, shot)
            i += 1

    def check_entry_shown(self) -> None:
        """Первый кадр новой зоны не «уже внутри»: вход виден (match on action)."""
        for i, shot in enumerate(self.shots):
            entry = self.entry_door(i)
            if not entry or not self.owned(shot) or _cut_is_soft(shot):
                continue
            text = _shot_text(shot)
            if _ENTER_RE.search(text):
                continue
            m = _ALREADY_IN_RE.search(text)
            if not m:
                continue
            who = next(
                (p["кто"] for p in shot.get("люди") or [] if p.get("кто") != "герой"),
                "",
            )
            rest = (text[: m.start()] + text[m.end():]).strip(" ,;")
            if who and rest.casefold().startswith(who.casefold()):
                rest = rest[len(who):].strip(" ,;")
            door = accusative(self.plan._prop_name(entry[0]))
            head = f"{who} входит через {door}" if who else f"входит через {door}"
            shot["действие"] = f"{head}, {rest}" if rest else head
            move = _OPPOSITE.get(entry[1], "")
            for row in shot.get("люди") or []:
                if row.get("кто") == who and move and not row.get("движется"):
                    row["движется"] = move
                    row.setdefault("лицом", move)
            self.issues.append(_issue(
                shot, "уже_внутри",
                f"«{text[:60]}»: герой не может быть «уже» внутри — "
                "показан вход через дверь",
                fixed=True,
            ))

    def _insert_threshold(self, i: int, door: str) -> bool:
        prev = self.shots[i - 1]
        if prev.get("вставлен") == "порог":
            return False
        halves = _split_vo(str(prev.get("закадр") or ""))
        if halves is None:
            return False
        door_name = self.plan._prop_name(door)
        who = next(
            (p["кто"] for p in prev.get("люди") or [] if p.get("кто") != "герой"),
            "",
        )
        acc = accusative(door_name)
        step = f"{who} открывает {acc}" if who else f"рука открывает {acc}"
        side = self.plan.door_side(prev["_zk"], door)
        new = copy.deepcopy(prev)
        for k in ("_start", "раскладка", "_cam_given"):
            new.pop(k, None)
        new.update({
            "действие": step,
            "объект": classify_object(step),
            "закадр": halves[1],
            "план": "СРЕДНИЙ",
            "линза_мм": 50,
            "ракурс": "3/4",
            "движение": "статика",
            "стык": "cut",
            "вставлен": "порог",
            "меняет": [{"предмет": door_name, "состояние": OPEN}],
            "люди": [
                {"кто": p["кто"], "где": side, "лицом": side}
                for p in prev.get("люди") or []
            ][:1],
        })
        sid = str(prev.get("id") or "")
        m = _ID_RE.match(sid)
        if sid and not m:
            new["id"] = f"{sid}-порог"
        prev["закадр"] = halves[0]
        cur = self.shots[i]
        if self.owned(cur):
            cur["стык"] = "cut_on_action"
        self.shots.insert(i, new)
        if m:
            _renumber_scene(self.shots, m.group(1))
        return True

    def check_path(self) -> None:
        last: dict[str, tuple[str, dict[str, str]]] = {}
        for shot in self.shots:
            zk = shot["_zk"]
            for row in shot.get("люди") or []:
                prev = last.get(row["кто"])
                if prev and prev[0] == zk and self.owned(shot):
                    prow = prev[1]
                    move = prow.get("движется", "")
                    if (
                        move in DIRS
                        and row.get("где") == prow.get("где")
                        and prow.get("где") != move
                    ):
                        row["где"] = _step_toward(prow.get("где", CENTER), move)
                        self.issues.append(_issue(
                            shot, "на_месте",
                            f"{row['кто']} шёл, а стоит там же — дальше по пути "
                            f"({_AT[row['где']]})",
                            fixed=True,
                        ))
                last[row["кто"]] = (zk, row)

    def _lateral_ok(self, shot: dict[str, Any], cam: dict[str, str],
                    sides: dict[str, int]) -> bool:
        for row in shot.get("люди") or []:
            if row.get("движется") or row.get("где") not in DIRS:
                continue
            want = sides.get(row["кто"])
            got = lateral(cam["смотрит"], row["где"])
            if want and got and want != got:
                return False
        return True

    def vary_shot_sizes(self) -> None:
        """Одинаковый план подряд или прыжок ОБЩИЙ↔ДЕТАЛЬ — сдвинуть крупность."""
        prev: dict[str, Any] | None = None
        for shot in self.shots:
            if not self.owned(shot) or prev is None or prev.get("_zk") != shot.get("_zk"):
                prev = shot
                continue
            cur_r = _plan_rung(shot.get("план"))
            prev_r = _plan_rung(prev.get("план"))
            if cur_r is None or prev_r is None:
                prev = shot
                continue
            delta = abs(cur_r - prev_r)
            if delta == 0:
                nxt = _step_away_plan(
                    str(shot.get("план") or ""), str(shot.get("объект") or "")
                )
                if nxt != shot.get("план"):
                    shot["план"] = nxt
                    self.issues.append(_issue(
                        shot, "план",
                        f"тот же план, что у прошлого кадра — {nxt}",
                        fixed=True,
                    ))
            elif delta >= 3:
                mid = (prev_r + cur_r) // 2
                if mid == prev_r:
                    mid += 1 if cur_r > prev_r else -1
                nxt = _plan_name(mid)
                shot["план"] = nxt
                self.issues.append(_issue(
                    shot, "план",
                    f"прыжок крупности {prev.get('план')} → {_plan_name(cur_r)} — {nxt}",
                    fixed=True,
                ))
            prev = shot

    def vary_repeated_camera(self) -> None:
        """Правило 30°: смена плана или тот же объект — угол не тот же.

        Компас: соседняя сторона (90°), если ось 180° и стороны людей держатся.
        Имя ракурса: ступень ≥30°, если совпало с прошлым кадром.
        """
        for i in range(1, len(self.shots)):
            prev, cur = self.shots[i - 1], self.shots[i]
            if prev["_zk"] != cur["_zk"] or not self.owned(cur):
                continue
            plan_changed = _key(cur.get("план")) != _key(prev.get("план"))
            same_subject = _key(cur.get("объект")) == _key(prev.get("объект"))
            if not plan_changed and not same_subject:
                continue
            if cur["камера"] == prev["камера"]:
                sides = self._people_sides(i)
                where = cur["камера"]["где"]
                for side in (d for d in DIRS if d not in (where, _OPPOSITE[where])):
                    cam = _turn(cur["камера"], side)
                    if (
                        self._angle_keeps_sides(cur, cam, sides)
                        and self._movers_ok(prev, cur, cam)
                    ):
                        cur["камера"] = cam
                        why = (
                            "смена плана без смены угла — камера "
                            if plan_changed
                            else "тот же угол, что у прошлого кадра — камера "
                        )
                        self.issues.append(_issue(
                            cur, "ракурс",
                            f"{why}{_FROM[side]} (≥30°)",
                            fixed=True,
                        ))
                        break
            prev_ang = prev.get("ракурс")
            cur_ang = cur.get("ракурс")
            if not angles_under_30(prev_ang, cur_ang):
                continue
            nxt = next_named_angle_30(
                cur_ang or prev_ang or "фронт", cur.get("объект")
            )
            if named_angle_token(nxt) == named_angle_token(cur_ang):
                nxt = next_named_angle_30(nxt, cur.get("объект"))
            cur["ракурс"] = _with_angle_token(cur_ang, nxt)
            self.issues.append(_issue(
                cur, "ракурс",
                f"правило 30°: ракурс {named_angle_token(cur_ang) or 'не задан'} → {nxt}",
                fixed=True,
            ))

    def _angle_keeps_sides(
        self,
        shot: dict[str, Any],
        cam: dict[str, str],
        sides: dict[str, int],
    ) -> bool:
        """90° не ломает лево/право и не прячет человека за камеру."""
        look = cam["смотрит"]
        for row in shot.get("люди") or []:
            if row.get("движется") or row.get("где") not in DIRS:
                continue
            want = sides.get(row["кто"])
            got = lateral(look, row["где"])
            if want and got != want:
                return False
            if screen_of(look, row["где"]) == "за камерой":
                return False
        return True

    def _movers_ok(self, prev: dict[str, Any], cur: dict[str, Any],
                   cam: dict[str, str]) -> bool:
        pm = {p["кто"]: p.get("движется") for p in prev.get("люди") or []}
        for row in cur.get("люди") or []:
            move = row.get("движется")
            if not move or pm.get(row["кто"]) != move:
                continue
            a = lateral(prev["камера"]["смотрит"], move)
            b = lateral(cam["смотрит"], move)
            if a and b and a != b:
                return False
        return True

    def _people_sides(self, i: int) -> dict[str, int]:
        """Последняя сторона экрана каждого человека в зоне кадра ``i``."""
        zk = self.shots[i]["_zk"]
        sides: dict[str, int] = {}
        for shot in self.shots[:i]:
            if shot["_zk"] != zk:
                sides = {}
                continue
            if _cut_is_soft(shot):
                sides = {}
            for row in shot.get("люди") or []:
                if row.get("движется") or row.get("где") not in DIRS:
                    continue
                side = lateral(shot["камера"]["смотрит"], row["где"])
                if side:
                    sides[row["кто"]] = side
        return sides

    def check_screen_direction(self) -> None:
        for i in range(1, len(self.shots)):
            prev, cur = self.shots[i - 1], self.shots[i]
            if _cut_is_soft(cur) or not self.owned(cur):
                continue
            if not self._movers_ok(prev, cur, cur["камера"]):
                mover = next(
                    (r for r in cur.get("люди") or [] if r.get("движется")), {}
                )
                move = mover.get("движется", "")
                if prev["_zk"] == cur["_zk"]:
                    cur["камера"] = dict(prev["камера"])
                else:
                    cam = cur["камера"]
                    cur["камера"] = {
                        "где": _OPPOSITE[cam["где"]],
                        "смотрит": _OPPOSITE[cam["смотрит"]],
                    }
                self.issues.append(_issue(
                    cur, "направление",
                    f"{mover.get('кто', 'герой')} бежал "
                    f"{motion_on_screen(prev['камера']['смотрит'], move)}, "
                    "в этом кадре экран перевернулся — камера с той же стороны",
                    fixed=True,
                ))

    def check_axis(self) -> None:
        """Ось 180°: стоящий человек не меняет сторону экрана внутри зоны."""
        for i in range(1, len(self.shots)):
            cur = self.shots[i]
            if _cut_is_soft(cur) or not self.owned(cur):
                continue
            sides = self._people_sides(i)
            if not sides or self._lateral_ok(cur, cur["камера"], sides):
                continue
            cam = cur["камера"]
            flipped = {"где": _OPPOSITE[cam["где"]], "смотрит": _OPPOSITE[cam["смотрит"]]}
            if not self._lateral_ok(cur, flipped, sides):
                continue
            cur["камера"] = flipped
            names = ", ".join(
                r["кто"] for r in cur.get("люди") or [] if r["кто"] in sides
            )
            self.issues.append(_issue(
                cur, "ось_180",
                f"{names}: сторона экрана перевернулась — камера перешла ось, "
                f"вернул {_FROM[flipped['где']]}",
                fixed=True,
            ))

    # 5. раскладка
    def _hide_arrivals_at_start(
        self, shot: dict[str, Any], start_states: dict[str, str]
    ) -> None:
        """Кладёт/ставит: на старте предмета ещё нет на столе."""
        for c in shot.get("меняет") or []:
            if not isinstance(c, dict):
                continue
            st = str(c.get("состояние") or "")
            if not _is_arrival(shot, st):
                continue
            pk = self.plan.find_prop(shot["_zk"], c.get("предмет") or "")
            if pk:
                start_states[pk] = ABSENT

    def compile_layouts(self) -> None:
        states = self.plan.initial_states()
        prev_owned: dict[str, Any] | None = None
        for shot in self.shots:
            start_states = dict(states)
            self._hide_arrivals_at_start(shot, start_states)
            shot["_start"] = start_states
            end_states = dict(states)
            self._apply_changes(end_states, shot)
            shot["_end"] = end_states
            self._apply_changes(states, shot)
            if self.owned(shot):
                freeze = needs_freeze(shot)
                shot["точность"] = freeze
                if freeze:
                    freeze_s, freeze_e = _freeze_pair(shot)
                    shot["старт"] = freeze_s
                    shot["конец"] = freeze_e
                shot["раскладка"] = self._layout(
                    shot, prev_owned, freeze=freeze
                )
                prev_owned = shot

    def _prop_buckets(
        self,
        *,
        look: str,
        states: dict[str, str],
        props: dict[str, dict[str, Any]],
        zone_what: str,
        close: bool,
        focals: set[str],
    ) -> tuple[dict[str, list[str]], set[str]]:
        buckets: dict[str, list[str]] = {}
        shown: set[str] = set()
        for pk, prop in props.items():
            if states.get(pk) == ABSENT:
                continue
            spot = screen_of(look, prop["где"])
            if spot == "за камерой":
                continue
            if close and focals and pk not in focals:
                continue
            shown.add(pk)
            buckets.setdefault(spot, []).append(
                _prop_label(prop, states, pk, zone_what=zone_what)
            )
        return buckets, shown

    @staticmethod
    def _bucket_lines(buckets: dict[str, list[str]]) -> list[str]:
        lines: list[str] = []
        for spot in ("в глубине", "слева", "справа", "в центре"):
            if buckets.get(spot):
                lines.append(f"{spot.capitalize()}: {', '.join(buckets[spot])}.")
        return lines

    def _people_lines(
        self,
        shot: dict[str, Any],
        people: list[dict[str, Any]],
        look: str,
        *,
        at_end: bool,
    ) -> list[str]:
        lines: list[str] = []
        for row in people:
            where = row.get("где", CENTER)
            bits = [f"{row['кто']} — {_AT.get(where, 'в центре')}"]
            spot = screen_of(look, where)
            if spot == "за камерой":
                bits.append("у камеры, спиной или плечом в кадре")
            elif spot != "в центре":
                bits.append(f"на экране {spot}")
            if at_end or row.get("_arrived"):
                lines.append(", ".join(bits) + ".")
                continue
            move = row.get("движется")
            if move:
                bits.append(
                    f"движется на {move}, на экране {motion_on_screen(look, move)}"
                )
            elif row.get("лицом"):
                face = _facing_on_screen(look, row["лицом"])
                if face == "спиной к камере" and _is_close(shot):
                    face = "в три четверти со спины, лицо видно в профиль"
                if face:
                    bits.append(face)
            lines.append(", ".join(bits) + ".")
        return lines

    def _layout(
        self,
        shot: dict[str, Any],
        prev: dict[str, Any] | None,
        *,
        freeze: bool | None = None,
    ) -> str:
        zk = shot["_zk"]
        zone = self.plan.zones[zk]
        cam = shot["камера"]
        look = cam["смотрит"]
        start = shot.get("_start") or {}
        end = shot.get("_end") or start
        props = zone["предметы"]
        rung = _plan_rung(shot.get("план"))
        if rung is None:
            rung = 2
        plan_name = str(shot.get("план") or _plan_name(rung))
        obj = _key(shot.get("объект"))
        close = rung >= 3
        detail = rung >= 4
        if freeze is None:
            freeze = needs_freeze(shot)
        freeze_start, freeze_end = ("", "")
        if freeze:
            freeze_start, freeze_end = _freeze_pair(shot)
        zwhat = str(zone.get("что") or "").strip()
        focals = _focal_prop_keys(shot, props)
        if detail and not focals:
            focals = {
                pk for pk, prop in props.items()
                if str(prop.get("где") or "") == CENTER
            }
        start_buckets, shown = self._prop_buckets(
            look=look,
            states=start,
            props=props,
            zone_what=zwhat,
            close=close,
            focals=focals,
        )
        end_buckets, _ = self._prop_buckets(
            look=look,
            states=end,
            props=props,
            zone_what=zwhat,
            close=close,
            focals=focals,
        )
        show_people = not (detail and obj in {"предмет", "взгляд"})
        arrival = ABSENT in start.values()
        act = " ".join(str(shot.get("действие") or "").split())
        if freeze:
            parts: list[str] = [
                f"СТАРТ (первый кадр, до действия): {freeze_start}.",
                f"План {plan_name}.",
            ]
        else:
            parts = [
                f"ДЕЙСТВИЕ (процесс в одном кадре): {act}. "
                "Картинка = этот процесс в разгаре, не пара «до/после».",
                f"План {plan_name}.",
            ]
        if not close:
            extra = f": {zwhat}" if zwhat and not arrival else ""
            parts.append(f"Зона «{zone['id']}»" + extra + ".")
        else:
            parts.append(
                f"Зона «{zone['id']}», {plan_name} — не общий вид помещения."
            )
        parts.append(f"Камера {_FROM.get(cam['где'], '')}, смотрит на {look}.")
        ang = str(shot.get("ракурс") or "").strip()
        if ang:
            parts.append(f"Ракурс {ang}.")
        vis_buckets = start_buckets if freeze else end_buckets
        if vis_buckets:
            parts.append("Предметы на старте:" if freeze else "Предметы в кадре:")
            parts.extend(self._bucket_lines(vis_buckets))
        else:
            parts.append("Предметов площадки в кадре нет.")
        if show_people:
            parts.extend(
                self._people_lines(shot, list(shot.get("люди") or []), look, at_end=False)
            )
        elif any(shot.get("люди") or []):
            parts.append("В кадре руки и жест, фигура целиком не читается.")
        hidden = [
            str(prop.get("id") or pk)
            for pk, prop in props.items()
            if pk not in shown
        ]
        if close and hidden:
            parts.append(f"Не видно при этой крупности: {', '.join(hidden)}.")
        if freeze:
            parts.append(f"КОНЕЦ (последний кадр действия): {freeze_end}.")
            end_people = _people_at_end(shot) if show_people else []
            if end_buckets:
                parts.append("Предметы в конце:")
                parts.extend(self._bucket_lines(end_buckets))
            if end_people:
                parts.extend(self._people_lines(shot, end_people, look, at_end=True))
        changes = [
            f"{c['предмет']} → {c['состояние']}" for c in shot.get("меняет") or []
        ]
        if changes:
            parts.append(f"В кадре меняется: {'; '.join(changes)}.")
        if prev is None:
            parts.append("Референса нет — первый кадр места.")
        elif prev.get("_zk") != shot.get("_zk"):
            parts.append(
                f"В референсе (другое место): {_cam_brief(prev)}. "
                "Это новый кадр места, не punch-in с прошлого."
            )
        else:
            parts.append(f"В референсе: {_cam_brief(prev)}.")
            parts.append(
                f"Смена: {_cam_brief(shot)}. "
                "Угол камеры относительно референса ≥30°."
            )
        return " ".join(parts)


_TMP_KEYS = ("_zk", "_start", "_end", "_ro", "_cell", "_cam_given")


def _merge_plans(raws: list[Any]) -> dict[str, Any]:
    """Площадки нескольких ячеек → одна (зоны/проходы/люди без дублей)."""
    merged: dict[str, Any] = {"зоны": [], "проходы": [], "люди": []}
    zones: dict[str, dict[str, Any]] = {}
    for raw in raws:
        plan = parse_plan(raw).to_dict()
        for z in plan["зоны"]:
            k = _key(z["id"])
            if k not in zones:
                zones[k] = {"id": z["id"], "что": z.get("что") or "", "предметы": []}
                merged["зоны"].append(zones[k])
            have = {_key(p["id"]) for p in zones[k]["предметы"]}
            zones[k]["предметы"].extend(
                p for p in z["предметы"] if _key(p["id"]) not in have
            )
            if not zones[k]["что"] and z.get("что"):
                zones[k]["что"] = z["что"]
        for p in plan["проходы"]:
            if p not in merged["проходы"]:
                merged["проходы"].append(p)
        names = {r["кто"] for r in merged["люди"]}
        merged["люди"].extend(r for r in plan["люди"] if r["кто"] not in names)
    return merged


def apply_scene_plan_cells(
    cells: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    """Несколько ячеек подряд: ``[{"key", "shots", "plan", "owned"}]``.

    Кадры ``owned=False`` — контекст соседей (двери, путь, ось), их не
    правим и не возвращаем. Кадры своих ячеек правятся на месте (в т.ч.
    вставка кадра на пороге). Возвращает (общая площадка, проблемы по key).
    """
    raws = [c.get("plan") for c in cells if c.get("plan")]
    plan = parse_plan(_merge_plans(raws)) if raws else ScenePlan()
    flat: list[dict[str, Any]] = []
    for c in cells:
        rows = [s for s in c.get("shots") or [] if isinstance(s, dict)]
        if c.get("owned"):
            c["shots"][:] = rows
        else:
            rows = copy.deepcopy(rows)
        for s in rows:
            s["_cell"] = c["key"]
            if not c.get("owned"):
                s["_ro"] = True
            flat.append(s)
    issues_by: dict[str, list[dict[str, Any]]] = {str(c["key"]): [] for c in cells}
    if flat:
        sim = _Sim(plan, flat)
        sim.assign_zones()
        sim.derive_passages()
        sim.assign_cameras()
        sim.assign_people()
        sim.infer_changes()
        sim.check_doors()
        sim.check_entry_shown()
        sim.check_path()
        sim.check_axis()
        sim.vary_shot_sizes()
        sim.vary_repeated_camera()
        sim.check_screen_direction()
        sim.compile_layouts()
        for c in cells:
            if not c.get("owned"):
                continue
            mine = [s for s in flat if s.get("_cell") == c["key"]]
            if len(mine) != len(c["shots"]) and any("порядок" in s for s in mine):
                for n, s in enumerate(mine, start=1):
                    s["порядок"] = n
            c["shots"][:] = mine
        for it in sim.issues:
            shot = it.pop("_shot")
            key = str(shot.get("_cell"))
            if (shot.get("_ro") or key not in issues_by) and it.get("исправлено"):
                continue
            cell_shots = next(
                (c["shots"] for c in cells if str(c["key"]) == key and c.get("owned")),
                [],
            )
            it["кадр"] = next(
                (n for n, s in enumerate(cell_shots, start=1) if s is shot), 0
            )
            if shot.get("id"):
                it["id"] = str(shot["id"])
            issues_by.setdefault(key, []).append(it)
        for s in flat:
            for k in _TMP_KEYS:
                s.pop(k, None)
    out = plan.to_dict()
    if plan.derived:
        out["выведено_кодом"] = list(dict.fromkeys(plan.derived))
    return out, issues_by


def apply_scene_plan(
    shots: list[Any], plan_raw: Any
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Нормализовать площадку и кадры одной ячейки, починить физику, раскладка.

    Мутирует ``shots`` (может вставить кадр на пороге). Возвращает
    (площадка для attrs, список проблем с флагом ``исправлено``).
    Повторный прогон на своём выходе ничего не меняет.
    """
    cell = {"key": "_", "shots": shots, "plan": plan_raw, "owned": True}
    base, issues_by = apply_scene_plan_cells([cell])
    issues = issues_by.get("_", [])
    return with_plan_notes(base, plan_raw, issues, shots), issues


def with_plan_notes(
    base: dict[str, Any],
    old_raw: Any,
    issues: list[dict[str, Any]],
    shots: list[Any] | None = None,
) -> dict[str, Any]:
    """Площадка + история правок кода; ``shots`` — отсечь заметки чужих кадров."""
    out = dict(base)
    old = old_raw if isinstance(old_raw, dict) else {}
    derived = [*(old.get("выведено_кодом") or []), *(base.get("выведено_кодом") or [])]
    if derived:
        out["выведено_кодом"] = list(dict.fromkeys(str(x) for x in derived))
    ids = (
        {str(s.get("id")) for s in shots if isinstance(s, dict) and s.get("id")}
        if shots
        else None
    )
    kept = notes_for_shots(list(old.get("исправлено_кодом") or []), ids)
    notes = [*kept, *plan_fix_notes(issues)]
    if notes:
        out["исправлено_кодом"] = list(dict.fromkeys(str(x) for x in notes))[-30:]
    return out


def _note(it: dict[str, Any]) -> str:
    sid = str(it.get("id") or "")
    where = f"кадр {it['кадр']}" + (f" [{sid}]" if sid else "")
    return f"{where}: {it['текст']}"


_NOTE_ID_RE = re.compile(r"^кадр \d+ \[([^\]]+)\]:")


def plan_hard_reasons(issues: list[dict[str, Any]]) -> list[str]:
    return [_note(it) for it in issues if not it.get("исправлено")]


def plan_fix_notes(issues: list[dict[str, Any]]) -> list[str]:
    return [_note(it) for it in issues if it.get("исправлено")]


def notes_for_shots(notes: list[Any], shot_ids: set[str] | None) -> list[str]:
    """Заметки только про кадры этой ячейки (после разрезки seed-ячейки)."""
    out: list[str] = []
    for note in notes:
        m = _NOTE_ID_RE.match(str(note))
        if shot_ids is None or not m or m.group(1) in shot_ids:
            out.append(str(note))
    return out


# ── Схема сверху для отчёта ───────────────────────────────────────────


_SVG_POS = {
    "север": (60, 14), "юг": (60, 106), "запад": (14, 60), "восток": (106, 60),
    CENTER: (60, 60),
}
_SVG_COLS = 5
_SVG_CELL = 130
_SVG_ROW = 140
_CAM_ID_RE = re.compile(r"-K(\d+)$", re.I)


def _zones_for_svg(
    zones: list[dict[str, Any]],
    passages: list[Any],
    shots: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Не тащить всю карту фильма в каждую ячейку — только зоны кадров + соседи."""
    used = {_key(s.get("зона")) for s in (shots or []) if s.get("зона")}
    used.discard("")
    if not used:
        return zones
    extra: set[str] = set()
    for p in passages or []:
        if not isinstance(p, dict):
            continue
        a, b = _key(p.get("из")), _key(p.get("в"))
        if a in used or b in used:
            extra.add(a)
            extra.add(b)
    keep = used | extra
    filtered = [z for z in zones if _key(z.get("id")) in keep]
    return filtered or zones


def _cam_label(shot: dict[str, Any], fallback: int) -> str:
    sid = str(shot.get("id") or shot.get("shot_id") or "")
    m = _CAM_ID_RE.search(sid)
    if m:
        return f"К{m.group(1)}"
    try:
        n = int(shot.get("порядок") or 0)
    except (TypeError, ValueError):
        n = 0
    return f"К{n or fallback}"


def plan_svg(plan: dict[str, Any], shots: list[dict[str, Any]] | None = None) -> str:
    """Схема сверху: зоны сеткой, предметы по сторонам, камеры кадров."""
    zones_all = [z for z in (plan or {}).get("зоны") or [] if isinstance(z, dict)]
    if not zones_all:
        return ""
    passages = [p for p in (plan or {}).get("проходы") or [] if isinstance(p, dict)]
    zones = _zones_for_svg(zones_all, passages, shots)
    cells: list[str] = []
    arrows = {"север": "↑", "юг": "↓", "запад": "←", "восток": "→"}
    cols = min(_SVG_COLS, max(1, len(zones)))
    rows = (len(zones) + cols - 1) // cols
    at_slot: dict[tuple[int, str], int] = {}
    cam_n = 0
    for zi, zone in enumerate(zones):
        col, row = zi % cols, zi // cols
        ox, oy = col * _SVG_CELL, row * _SVG_ROW
        zid = str(zone.get("id") or "")
        cells.append(
            f'<rect x="{ox + 5}" y="{oy + 5}" width="110" height="110" rx="6" '
            'fill="#f7f7f5" stroke="#999"/>'
        )
        z_full = html.escape(zid)
        z_short = html.escape(zid[:22])
        cells.append(
            f'<text x="{ox + 60}" y="{oy + 128}" text-anchor="middle" '
            f'font-size="10" fill="#111"><title>{z_full}</title>{z_short}</text>'
        )
        for prop in zone.get("предметы") or []:
            if not isinstance(prop, dict):
                continue
            x, y = _SVG_POS.get(str(prop.get("где")), _SVG_POS[CENTER])
            door = _is_door(str(prop.get("id") or ""))
            color = "#b5651d" if door else "#555"
            pid = str(prop.get("id") or "")
            cells.append(
                f'<text x="{ox + x}" y="{oy + y + 3}" text-anchor="middle" '
                f'font-size="8" fill="{color}"><title>{html.escape(pid)}</title>'
                f"{html.escape(pid[:16])}</text>"
            )
        for shot in shots or []:
            if _key(shot.get("зона")) != _key(zid):
                continue
            cam = shot.get("камера")
            if not isinstance(cam, dict):
                continue
            where = str(cam.get("где") or "")
            if where not in _SVG_POS:
                continue
            cam_n += 1
            n_at = at_slot.get((zi, where), 0)
            at_slot[(zi, where)] = n_at + 1
            x, y = _SVG_POS[where]
            x = min(max(x + (n_at % 3 - 1) * 16, 22), 98)
            y = min(max(y + 12 + (n_at // 3) * 11, 22), 108)
            arrow = arrows.get(str(cam.get("смотрит") or ""), "•")
            cells.append(
                f'<text x="{ox + x}" y="{oy + y}" text-anchor="middle" '
                f'font-size="9" fill="#1f5fbf">'
                f"{html.escape(_cam_label(shot, cam_n))}{arrow}</text>"
            )
    total_w = _SVG_CELL * cols
    total_h = _SVG_ROW * rows + 16
    cells.append(
        f'<text x="8" y="{total_h - 4}" font-size="9" fill="#666">'
        "север ↑ · юг ↓ · запад ← · восток →</text>"
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="{total_h}" '
        f'viewBox="0 0 {total_w} {total_h}" class="plan-svg">{"".join(cells)}</svg>'
    )
