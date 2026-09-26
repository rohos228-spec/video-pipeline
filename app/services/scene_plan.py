"""План площадки сцены: зоны, проходы, двери, камера, люди — по компасу.

GPT (fw_action) пишет ``площадка`` на VO-ячейку, GPT (fw_shots) пишет в
каждом кадре ``зона`` / ``камера`` / ``люди`` / ``меняет``. Всё, что модель
не написала, код выводит из действия. Дальше код:

* ведёт состояния предметов (дверь закрыта, пока её не открыли в кадре);
* считает экран из направления камеры (смотрит на север → запад слева);
* чинит физику: проход через закрытую дверь (вставляет кадр на пороге),
  «к открытой двери» при закрытой, «идёт на месте», переворот направления
  движения на экране, переход оси 180° в паре;
* пишет в кадр ``раскладка`` — текст старта кадра для промта картинки.

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
    return {"id": name, "где": where, "состояние": _norm_state(state)}


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
                        {"id": p["id"], "где": p["где"], "состояние": p["состояние"]}
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

    def vary_repeated_camera(self) -> None:
        """Тот же ракурс и план подряд в зоне = скачок. Камеру на 90°."""
        for i in range(1, len(self.shots)):
            prev, cur = self.shots[i - 1], self.shots[i]
            if prev["_zk"] != cur["_zk"] or not self.owned(cur):
                continue
            if cur["камера"] != prev["камера"]:
                continue
            if _key(cur.get("план")) != _key(prev.get("план")):
                continue
            if cur.get("вставлен") or prev.get("вставлен"):
                continue
            sides = self._people_sides(i)
            where = cur["камера"]["где"]
            for side in (d for d in DIRS if d not in (where, _OPPOSITE[where])):
                cam = _turn(cur["камера"], side)
                if self._lateral_ok(cur, cam, sides) and self._movers_ok(prev, cur, cam):
                    cur["камера"] = cam
                    self.issues.append(_issue(
                        cur, "ракурс",
                        f"тот же ракурс и план, что у прошлого кадра — камера "
                        f"{_FROM[side]}",
                        fixed=True,
                    ))
                    break

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
    def compile_layouts(self) -> None:
        states = self.plan.initial_states()
        for shot in self.shots:
            shot["_start"] = dict(states)
            self._apply_changes(states, shot)
            if self.owned(shot):
                shot["раскладка"] = self._layout(shot)

    def _layout(self, shot: dict[str, Any]) -> str:
        zk = shot["_zk"]
        zone = self.plan.zones[zk]
        cam = shot["камера"]
        look = cam["смотрит"]
        start = shot.get("_start") or {}
        parts = [f"Зона «{zone['id']}»" + (f": {zone['что']}" if zone.get("что") else "") + "."]
        parts.append(f"Камера {_FROM.get(cam['где'], '')}, смотрит на {look}.")
        buckets: dict[str, list[str]] = {}
        for pk, prop in zone["предметы"].items():
            spot = screen_of(look, prop["где"])
            if spot == "за камерой":
                continue
            state = start.get(pk) or prop.get("состояние") or ""
            label = prop["id"] + (f" ({state})" if state else "")
            buckets.setdefault(spot, []).append(label)
        for spot in ("в глубине", "слева", "справа", "в центре"):
            if buckets.get(spot):
                parts.append(f"{spot.capitalize()}: {', '.join(buckets[spot])}.")
        for row in shot.get("люди") or []:
            where = row.get("где", CENTER)
            bits = [f"{row['кто']} — {_AT.get(where, 'в центре')}"]
            spot = screen_of(look, where)
            if spot == "за камерой":
                bits.append("у камеры, спиной или плечом в кадре")
            elif spot != "в центре":
                bits.append(f"на экране {spot}")
            move = row.get("движется")
            if move:
                bits.append(f"движется на {move}, на экране {motion_on_screen(look, move)}")
            elif row.get("лицом"):
                face = _facing_on_screen(look, row["лицом"])
                if face == "спиной к камере" and _is_close(shot):
                    face = "в три четверти со спины, лицо видно в профиль"
                if face:
                    bits.append(face)
            parts.append(", ".join(bits) + ".")
        changes = [
            f"{c['предмет']} → {c['состояние']}" for c in shot.get("меняет") or []
        ]
        if changes:
            parts.append(f"В кадре меняется: {'; '.join(changes)}.")
        return " ".join(parts)


_TMP_KEYS = ("_zk", "_start", "_ro", "_cell", "_cam_given")


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


def plan_svg(plan: dict[str, Any], shots: list[dict[str, Any]] | None = None) -> str:
    """Схема сверху: по квадрату на зону, предметы по сторонам, камеры кадров."""
    zones = [z for z in (plan or {}).get("зоны") or [] if isinstance(z, dict)]
    if not zones:
        return ""
    cells: list[str] = []
    w = 130
    arrows = {"север": "↑", "юг": "↓", "запад": "←", "восток": "→"}
    for zi, zone in enumerate(zones):
        ox = zi * w
        zid = str(zone.get("id") or "")
        cells.append(
            f'<rect x="{ox + 5}" y="5" width="110" height="110" rx="6" '
            'fill="#f7f7f5" stroke="#999"/>'
        )
        cells.append(
            f'<text x="{ox + 60}" y="128" text-anchor="middle" font-size="10">'
            f"{html.escape(zid[:22])}</text>"
        )
        for prop in zone.get("предметы") or []:
            x, y = _SVG_POS.get(str(prop.get("где")), _SVG_POS[CENTER])
            door = _is_door(str(prop.get("id") or ""))
            color = "#b5651d" if door else "#555"
            cells.append(
                f'<text x="{ox + x}" y="{y + 3}" text-anchor="middle" '
                f'font-size="8" fill="{color}">{html.escape(str(prop.get("id"))[:16])}</text>'
            )
        for si, shot in enumerate(shots or []):
            if _key(shot.get("зона")) != _key(zid):
                continue
            cam = shot.get("камера") or {}
            where = str(cam.get("где") or "")
            if where not in _SVG_POS:
                continue
            x, y = _SVG_POS[where]
            x = min(max(x, 24), 96)
            y = min(max(y, 24), 96)
            arrow = arrows.get(str(cam.get("смотрит") or ""), "•")
            cells.append(
                f'<text x="{ox + x + (si % 3 - 1) * 9}" y="{y + 12}" '
                f'text-anchor="middle" font-size="9" fill="#1f5fbf">'
                f"К{si + 1}{arrow}</text>"
            )
    total_w = w * len(zones)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_w}" height="136" '
        f'viewBox="0 0 {total_w} 136" class="plan-svg">{"".join(cells)}</svg>'
    )
