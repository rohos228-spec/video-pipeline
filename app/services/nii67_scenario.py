"""Автосценарий NII 67: мир, закон, шесть битов и раскадровка.

Правила лежат в ``prompts/templates/nii67/v2``. На дверь — один вызов текстовой
модели с маршрута ноды «Сценарий» (vibecode, по умолчанию gpt-5.6-sol).
Двери стартуют параллельно, вторая с паузой из шаблона. Поля карточки
заполняются и остаются редактируемыми. Оператор загружает исходное видео.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import threading
from collections.abc import Awaitable, Callable
from functools import cache
from pathlib import Path
from typing import Any

from loguru import logger

TEMPLATE_VERSION = "v2"
TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "prompts" / "templates" / "nii67" / TEMPLATE_VERSION

LlmFn = Callable[..., Awaitable[str]]
SleepFn = Callable[[float], Awaitable[None]]

_OUTCOME_ALIAS = {
    "death": "death",
    "гибель": "death",
    "смерть": "death",
    "escape": "escape",
    "побег": "escape",
    "transformation": "transformation",
    "превращение": "transformation",
}
_BEAT_KIND = {
    "entry": "world_frame",
    "law": "enter_action",
    "threat": "world_character",
    "peak": "character_action",
    "outcome": "gg_action",
    "hook": "exit",
}


class ScenarioGenerationError(RuntimeError):
    def __init__(self, door_id: str, errors: list[str], *, attempts: int) -> None:
        self.door_id = door_id
        self.errors = list(errors)
        self.attempts = attempts
        title = {"door1": "Дверь 1", "door2": "Дверь 2"}.get(door_id, "Сценарий")
        human = ", ".join(_human(code) for code in self.errors[:8]) or "проверка не пройдена"
        super().__init__(
            f"{title}: не удалось собрать сценарий за {attempts} {_attempts_word(attempts)} ({human})."
        )


def _attempts_word(count: int) -> str:
    tail = count % 10
    hundred = count % 100
    if tail == 1 and hundred != 11:
        return "попытку"
    if tail in {2, 3, 4} and hundred not in {12, 13, 14}:
        return "попытки"
    return "попыток"


@cache
def _read(name: str) -> str:
    return (TEMPLATE_DIR / name).read_text(encoding="utf-8")


@cache
def load_v2(name: str) -> dict[str, Any]:
    return json.loads(_read(name))


def _norm(text: str) -> str:
    cleaned = text.casefold().replace("ё", "е")
    cleaned = re.sub(r"[^a-zа-я0-9\s-]", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _scan(text: str) -> str:
    return text.casefold().replace("ё", "е")


def _hits(text: str, patterns: list[str]) -> bool:
    hay = _scan(text)
    return any(re.search(pattern.replace("ё", "е"), hay) for pattern in patterns)


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(_strings(item))
        return out
    if isinstance(value, list):
        out = []
        for item in value:
            out.extend(_strings(item))
        return out
    return []


def visualization_errors(text: str) -> list[str]:
    """Лексическая проверка визуализации. Идёт раньше стоп-листа."""
    found: list[str] = []
    for group in load_v2("visualization.json")["forbidden"]:
        if _hits(text, list(group["patterns"])):
            found.append(f"visual:{group['id']}")
    return found


def stoplist_errors(text: str) -> list[str]:
    found: list[str] = []
    for item in load_v2("stoplist.json")["items"]:
        if _hits(text, list(item["patterns"])):
            found.append(f"stoplist:{item['id']}")
    return found


PROVIDER_RETRY_S = 4.0
SELF_SCORE_FLOOR = 8.0
PROVIDER_TRIES = 5
HISTORY_PATH = Path(__file__).resolve().parents[2] / "data" / "nii67_history.json"
_HISTORY_LOCK = threading.Lock()


def categories() -> list[dict[str, Any]]:
    return list(load_v2("categories.json")["items"])


def category_ids() -> list[str]:
    return [str(item["id"]) for item in categories()]


def _category(cid: str) -> dict[str, Any]:
    for item in categories():
        if item["id"] == cid:
            return item
    return {"id": cid, "title": cid, "hint": ""}


def icon_library() -> dict[str, list[dict[str, Any]]]:
    return dict(load_v2("icons.json")["icons"])


def _icon_used(name: str, recent: list[dict[str, str]]) -> bool:
    """Точная дедупликация иконы по имени в окне последних 20 миров."""
    key = _norm(name)
    return any(_norm(item.get("icon", "")) == key for item in recent[-20:] if item.get("icon"))


_CREATURE_ATTACK = re.compile(
    r"(монстр|чудовищ|хищник|зверь|демон|дракон|акул|тираннозавр|динозавр)[а-я]*"
    r"|(кусает|укус|пасть|челюст|когт[ия]|хватает зубами|гонится|преследует|набрасывается|бросается на)"
)


def creature_attack(text: str) -> bool:
    return bool(_CREATURE_ATTACK.search(_scan(text)))


def plan_door(
    category: str,
    recent: list[dict[str, str]],
    *,
    avoid_outcome: str | None = None,
    rng: random.Random | None = None,
) -> tuple[str, list[str]]:
    """Назначить исход по весам и выбрать свежих кандидатов-икон из библиотеки."""
    roll = rng or random.Random()
    cfg = load_v2("icons.json")
    weights = dict(cfg["outcome_weights"])
    pool = [it for it in icon_library().get(category, []) if not _icon_used(it["name"], recent)]
    if len(pool) < 3:
        pool = list(icon_library().get(category, []))
    counts = {o: sum(1 for it in pool if o in it["outcomes"]) for o in weights}
    options = [o for o in weights if counts[o] >= 2 and o != avoid_outcome] or [
        o for o in weights if counts[o] >= 1
    ] or list(weights)
    outcome = roll.choices(options, weights=[weights[o] for o in options])[0]
    fit = [it["name"] for it in pool if outcome in it["outcomes"]] or [it["name"] for it in pool]
    roll.shuffle(fit)
    return outcome, fit[: int(cfg.get("candidates_per_door", 6))]


def load_history(path: Path | None = None) -> list[dict[str, Any]]:
    target = path or HISTORY_PATH
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []


def _save_history(items: list[dict[str, Any]], path: Path | None = None) -> None:
    target = path or HISTORY_PATH
    size = int(load_v2("categories.json").get("history_size", 12))
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(items[-size:], ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError as exc:
        logger.warning("nii67 history: не удалось записать ({})", type(exc).__name__)


def pick_categories(count: int = 2, *, path: Path | None = None, rng: random.Random | None = None) -> list[str]:
    """Выбрать разные категории, которых нет среди недавних, и сразу зарезервировать их."""
    roll = rng or random.Random()
    block = int(load_v2("categories.json").get("recent_block", 6))
    with _HISTORY_LOCK:
        hist = load_history(path)
        recent = [str(item.get("category") or "") for item in hist]
        last = recent[-block:]
        ids = category_ids()
        fam_of = {str(item["id"]): str(item.get("family") or "") for item in categories()}
        win = int(load_v2("categories.json").get("family_window", 6))
        used_fams = {fam_of.get(c, "") for c in recent[-win:]} - {""}
        cool = dict(load_v2("categories.json").get("cooldown", {}))
        ids = [cid for cid in ids if cid not in recent[-int(cool.get(cid, 0)):] or not cool.get(cid)]
        fresh = [cid for cid in ids if cid not in last and fam_of.get(cid, "") not in used_fams]
        if len(fresh) < count:
            fresh = [cid for cid in ids if cid not in last]
        roll.shuffle(fresh)
        group = {str(item["id"]): str(item.get("group") or item["id"]) for item in categories()}
        chosen: list[str] = []
        for cid in fresh:
            if len(chosen) < count and group[cid] not in {group[x] for x in chosen}:
                chosen.append(cid)
        for cid in fresh:
            if len(chosen) < count and cid not in chosen:
                chosen.append(cid)
        if len(chosen) < count:
            age = {cid: (recent[::-1].index(cid) if cid in recent else len(recent) + 1) for cid in ids}
            rest = sorted((cid for cid in ids if cid not in chosen), key=lambda cid: -age[cid])
            chosen += rest[: count - len(chosen)]
        hist.extend({"category": cid, "theme": "", "mechanic": "", "reserved": True} for cid in chosen)
        _save_history(hist, path)
    return chosen


def record_history(docs: list[dict[str, Any]], *, path: Path | None = None) -> None:
    with _HISTORY_LOCK:
        hist = load_history(path)
        for doc in docs:
            cid = str(doc.get("category") or "")
            entry = {
                "category": cid,
                "theme": str(doc.get("theme") or ""),
                "mechanic": str(doc.get("mechanic") or ""),
                "icon": str(doc.get("icon") or ""),
                "family": str(doc.get("family") or ""),
            }
            for item in reversed(hist):
                if item.get("reserved") and item.get("category") == cid:
                    item.clear()
                    item.update(entry)
                    break
            else:
                hist.append(entry)
        _save_history(hist, path)


def recent_worlds(path: Path | None = None) -> list[dict[str, str]]:
    return [
        {
            "theme": str(item.get("theme") or ""),
            "mechanic": str(item.get("mechanic") or ""),
            "category": str(item.get("category") or ""),
            "family": str(item.get("family") or ""),
            "icon": str(item.get("icon") or ""),
        }
        for item in load_history(path)
        if item.get("theme")
    ]


_DEATH_RETURN = re.compile(
    r"(?:(?:герой|он)\s+(?:[а-я]+\s+){0,3}(?:стоит|вернул|возвраща|выходит|вышел|вываливает|выпрыгивает|выбегает|вбегает|входит))"
    r"|невредим"
)


def finale_line_errors(outcome: str, line: str) -> list[str]:
    text = re.sub(r"(?<![а-я])не\s+[а-я]+", " ", _scan(line))
    if not _scan(line).strip():
        return ["finale_missing"]
    if outcome == "death" and _DEATH_RETURN.search(text):
        return ["finale_mismatch"]
    if outcome == "escape" and re.search(r"(погиб|мёртв|мертв|не вернул|исчез навсегда)", text):
        return ["finale_mismatch"]
    return []


def build_finale(gg: str, doc1: dict[str, Any], doc2: dict[str, Any]) -> dict[str, str]:
    """Финал из обоих исходов, без противоречий."""
    sides = []
    for side, doc in (("Левая", doc1), ("Правая", doc2)):
        outcome = _outcome(doc.get("outcome"))
        line = str(doc.get("finale_line") or "").strip().rstrip(".")
        line = re.sub(
            rf"^\s*(?:{re.escape(gg)}|герой)\s+(?:не\s+вернулся|спасся|вернулся\s+изменённым)\s*[;,.:—-]*\s*",
            "",
            line,
            flags=re.IGNORECASE,
        )
        head = {"death": f"{gg} не вернулся", "transformation": f"{gg} вернулся изменённым"}.get(outcome, f"{gg} спасся")
        sides.append(f"{side} дверь — {head}. {line[:1].upper() + line[1:]}." if line else f"{side} дверь — {head}.")
    outs = {_outcome(doc1.get("outcome")), _outcome(doc2.get("outcome"))}
    if outs == {"death"}:
        tail = "Комната пуста, обе двери закрыты."
    elif "death" in outs:
        tail = "В одной ветке героя больше нет, в другой он в комнате."
    else:
        tail = "Обе ветки заканчиваются в комнате."
    return {
        "standard": " ".join(sides) + " " + tail,
        "notes": (
            f"Левая дверь — [{doc1.get('category')}] {doc1.get('theme')}. Закон: {doc1.get('law')} Исход: {_outcome(doc1.get('outcome'))}. "
            f"Правая дверь — [{doc2.get('category')}] {doc2.get('theme')}. Закон: {doc2.get('law')} Исход: {_outcome(doc2.get('outcome'))}."
        ),
    }


def _finale_action(gg: str, door_docs: dict[str, dict[str, Any]]) -> tuple[str, str, str]:
    parts, bodies = [], []
    for side, door_id in (("левая", "door1"), ("правая", "door2")):
        outcome = _outcome((door_docs.get(door_id) or {}).get("outcome"))
        if outcome == "death":
            parts.append(f"{side} дверь закрывается пустой")
            bodies.append(f"{side}: героя нет, у порога след")
        elif outcome == "transformation":
            parts.append(f"из двери ({side}) выходит изменённый {gg}")
            bodies.append(f"{side}: тело героя изменено")
        else:
            parts.append(f"через дверь ({side}) {gg} вываливается в комнату")
            bodies.append(f"{side}: на герое след мира")
    start = "Обе преображённые двери закрыты, комната пуста"
    return start, "; ".join(parts).capitalize(), "; ".join(bodies)


def _human(code: str) -> str:
    if code.startswith("visual:"):
        gid = code.split(":", 1)[1]
        for group in load_v2("visualization.json")["forbidden"]:
            if group["id"] == gid:
                return str(group["hint"])
        if gid == "action":
            return "в действии нет простого физического движения"
    if code.startswith("stoplist:"):
        sid = code.split(":", 1)[1]
        for item in load_v2("stoplist.json")["items"]:
            if item["id"] == sid:
                return "запрещённая тема: " + str(item["title"])
    fixed = {
        "theme_weak": "мир слишком общий",
        "theme_dull": "мир из общего списка",
        "law_missing": "нет одного ясного закона",
        "law_many": "в мире больше одного закона",
        "outcome_invalid": "исход не гибель, побег или превращение",
        "outcome_unlinked": "исход не следует из закона",
        "peak_weak": "пик недостаточно яркий",
        "beats_count": "нужны шесть битов",
        "no_change": "старт кадра совпадает с концом",
        "bad_camera": "камера не из списка",
        "bad_camera_feel": "нет ощущения ручной камеры",
        "double_close": "два крупных плана подряд",
        "themes_same": "миры двух дверей слишком похожи",
        "json_invalid": "ответ не JSON",
        "llm_error": "модель не ответила",
        "frame_contract": "кадр не совпал с контрактом",
        "category_invalid": "категория мира не из списка",
        "category_mismatch": "мир не из назначенной категории",
        "mechanic_missing": "не названо ядро механики",
        "theme_repeat": "мир или механика повторяют недавнюю генерацию",
        "mechanic_same": "у дверей одно и то же ядро механики",
        "finale_mismatch": "финальная строка противоречит исходу двери",
        "finale_missing": "нет финальной строки",
        "doors_same_arc": "у двух дверей один исход и одна эмоция",
        "twist_missing": "нет неожиданного поворота в пике",
        "outcome_mismatch": "исход не тот, что назначен",
        "creature_attack": "исход — атака существа, а не действие природы мира",
        "self_score_missing": "нет самооценки self_scores",
        "escape_generic": "спасение шаблонное: герой должен спастись приёмом, использующим закон именно этого мира, а не «схватился за раму и вывалился»",
        "death_passive": "смерть пассивна: герой должен активно бороться, а поворот мира должен победить его попытку",
        "transform_not_solid": "превращение должно закончиться твёрдой плотной формой (камень, дерево, металл, воск, кукла), не светом/дымом/прозрачностью",
        "icon_not_candidate": "мир не из списка кандидатов",
        "twist_not_shown": "поворот не показан в пике",
    }
    if code in fixed:
        return fixed[code]
    if code.startswith("beat_id"):
        return "бит не на своём месте"
    if code.startswith("family_repeat"):
        return "семейство темы уже было в последних 3 сценариях (" + code.split(":", 1)[-1] + "): возьми другую стихию"
    if code.startswith("self_score_low"):
        return "самооценка ниже 8 (" + code.split(":", 1)[-1] + "): усили эмоцию, поворот и ставки"
    if code.startswith("frames_count"):
        return "у бита должно быть 1–3 кадра"
    if code.startswith("visual_changes"):
        return "нет видимого изменения"
    return code


def _outcome(value: Any) -> str:
    raw = str(value or "").strip().casefold().replace("ё", "е")
    return _OUTCOME_ALIAS.get(raw, raw)


def _stems(text: str) -> set[str]:
    return {word[:5] for word in re.findall(r"[а-яa-z]{5,}", _norm(text))}


def themes_conflict(left: str, right: str) -> bool:
    a, b = _norm(left), _norm(right)
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    if len(ta & tb) / len(ta | tb) >= 0.5:
        return True
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return len(small) >= 3 and small <= big


def _changes(raw: Any, keys: list[str]) -> dict[str, str]:
    found = {key: "" for key in keys}
    if isinstance(raw, dict):
        for key in keys:
            found[key] = str(raw.get(key) or "").strip()
        return found
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, str) or ":" not in item:
                continue
            key, _, value = item.partition(":")
            key = key.strip().casefold()
            if key in found:
                found[key] = value.strip()
    return found


def _canon_token(value: Any, allowed: list[str]) -> str:
    raw = str(value or "").strip()
    if raw.upper() == "POV" and "POV" in allowed:
        return "POV"
    for item in allowed:
        if item.casefold() == raw.casefold():
            return item
    return raw


def _words(text: str) -> list[str]:
    return re.findall(r"[а-яa-z]+", _scan(text))


def nonvisual_verbs(text: str) -> list[str]:
    stems = [_scan(stem) for stem in load_v2("visualization.json")["nonvisual_verbs"]]
    return [word for word in _words(text) if any(word.startswith(stem) for stem in stems)]


def has_verb(text: str) -> bool:
    """Есть ли в тексте глагол настоящего времени (по окончаниям)."""
    cfg = load_v2("visualization.json")
    endings = sorted({_scan(item) for item in cfg["verb_endings"]}, key=len, reverse=True)
    nouns = {_scan(item) for item in cfg["noun_exceptions"]}
    for word in _words(text):
        if len(word) < 4 or word in nouns:
            continue
        if any(word.endswith(end) and len(word) - len(end) >= 2 for end in endings):
            return True
    return False


def _physical(action: str) -> bool:
    """Видимое действие: есть глагол и нет невидимых глаголов (думает, слышит...)."""
    return has_verb(action) and not nonvisual_verbs(action)


_CAMERA_ALIAS = {
    "wide": "far", "long": "far", "establishing": "far", "extreme wide": "far", "общий": "far", "дальний": "far",
    "medium": "mid", "middle": "mid", "средний": "mid", "medium shot": "mid", "over-shoulder": "side",
    "over the shoulder": "side", "profile": "side", "боковой": "side", "сбоку": "side",
    "close-up": "close", "closeup": "close", "close up": "close", "extreme close": "close", "крупный": "close",
    "pov": "POV", "first person": "POV", "от первого лица": "POV",
}
_FEEL_BY_BEAT = {"peak": "handheld_action", "threat": "handheld_action", "outcome": "handheld_intimate"}
EXAMPLE_THEME = {"theme": "Помпеи в день извержения Везувия", "mechanic": "Везувий хоронит город пеплом", "category": "history"}


def _outcome_linked(doc: dict[str, Any]) -> bool:
    beats = doc.get("beats") if isinstance(doc.get("beats"), list) else []
    beat_text = " ".join(str(b.get("text") or "") for b in beats if isinstance(b, dict) and b.get("id") in ("law", "threat", "peak"))
    src = " ".join([str(doc.get(k) or "") for k in ("law", "theme", "mechanic", "twist", "icon")] + [beat_text])
    linked = {w[:4] for w in _stems(src)} - {"геро", "кото", "кадр", "всех", "будт"}
    return bool(linked & {w[:4] for w in _stems(str(doc.get("outcome_process") or ""))})


def theme_families(text: str) -> set[str]:
    folded = str(text or "").casefold().replace("ё", "е")
    fams = load_v2("categories.json").get("families", {})
    return {name for name, stems in fams.items() if any(st in folded for st in stems)}


_ATTEMPT = re.compile(r"(пыта|хвата|прыга|бросает|бежит|кидает|тянет|цепля|ныря|отталкива|разбива|зажима|рвёт|рвет|ползёт|ползет|карабка|упира|закрыва|прикрыва|сбива|бьёт|бьет|вырыва|толка|разгоня|отбива|режет|лезет)")
_GENERIC_ESCAPE = re.compile(r"(хвата\w* за (раму|косяк|порог|дверн)|вывалива|выкатыва|вываливается обратно|падает обратно в комнату|запрыгивает обратно)")
FAMILIES = ("cold", "water", "fire", "electric", "light", "gravity", "time", "art", "body", "space", "mirror", "dream", "game", "earth", "none")
JUDGE_SYSTEM = (
    "Ты строгий редактор сценариев. Отвечай только JSON без пояснений: "
    '{"family":"<одно из: ' + "|".join(FAMILIES) + '>",'
    '"active_struggle":true|false,"world_trick_escape":true|false,"reason":"<до 15 слов>"}. '
    "family — главная стихия/природа мира. active_struggle — в угрозе/пике герой сам активно пытается спастись, "
    "а поворот, рождённый природой мира, побеждает именно эту попытку (для смерти). "
    "world_trick_escape — спасение достигнуто приёмом, который использует закон именно этого мира и невозможен в другом мире; "
    "«схватился за раму и вывалился», «прыгнул в дверь», «убежал» — это false."
)


async def judge_door(llm: "LlmFn", doc: dict[str, Any], door_id: str) -> dict[str, Any]:
    beats = doc.get("beats") if isinstance(doc.get("beats"), list) else []
    story = "\n".join(f"{b.get('id')}: {b.get('text')}" for b in beats if isinstance(b, dict))
    prompt = (
        f"Мир: {doc.get('theme')}\nЗакон: {doc.get('law')}\nИсход: {_outcome(doc.get('outcome'))}\n"
        f"Поворот: {doc.get('twist')}\nПроцесс исхода: {doc.get('outcome_process')}\n{story}"
    )
    raw = await llm(prompt, system=JUDGE_SYSTEM, door_id=door_id + "-judge")
    data = parse_scenario_json(raw)
    fam = str(data.get("family") or "none").strip().lower()
    data["family"] = fam if fam in FAMILIES else "none"
    return data


def judge_errors(verdict: dict[str, Any], outcome: str, recent: list[dict[str, str]] | None) -> list[str]:
    errs: list[str] = []
    if outcome == "death" and verdict.get("active_struggle") is False:
        errs.append("death_passive")
    if outcome == "escape" and verdict.get("world_trick_escape") is False:
        errs.append("escape_generic")
    fam = verdict.get("family") or "none"
    window = int(load_v2("categories.json").get("family_window", 6))
    seen = {str(r.get("family") or "") for r in (recent or []) if not r.get("reserved")} if recent else set()
    seen = {str(r.get("family") or "") for r in [x for x in (recent or []) if not x.get("reserved")][-window:]}
    if fam not in ("none", "") and fam in seen:
        errs.append("family_repeat:" + fam)
    return errs


_NOT_SOLID = re.compile(r"(свет[ао]?в|светов,|из света|сияни|прозрачн|полупрозрачн|электрич|молни|дым|газ|туман|пар[аоуы]? |призрач|голограм|пиксел|точек|мерца)")


def repair_door_doc(doc: dict[str, Any]) -> list[str]:
    """Чинит формальные ошибки модели вместо лишних попыток. Возвращает список правок."""
    fixes: list[str] = []
    contract = load_v2("frame_contract.json")
    cams, feels = list(contract["cameras"]), list(contract["feels"])
    order = [str(item["id"]) for item in load_v2("beats.json")["beats"]]
    beats = doc.get("beats")
    if isinstance(beats, dict):
        beats = [dict(v, id=k) if isinstance(v, dict) else v for k, v in beats.items()]
        doc["beats"] = beats
        fixes.append("beats_dict")
    if not isinstance(beats, list):
        return fixes
    beats = [b for b in beats if isinstance(b, dict)]
    ids = [str(b.get("id") or "").strip().lower() for b in beats]
    if sorted(ids) == sorted(order) and ids != order:
        beats.sort(key=lambda b: order.index(str(b.get("id")).strip().lower()))
        fixes.append("beats_reordered")
    elif len(beats) == len(order) and ids != order:
        for b, bid in zip(beats, order):
            b["id"] = bid
        fixes.append("beat_ids_assigned")
    for b in beats:
        b["id"] = str(b.get("id") or "").strip().lower()
        frames = b.get("frames") if isinstance(b.get("frames"), list) else []
        if len(frames) > 3:
            b["frames"] = frames[:3]
            fixes.append("frames_trimmed")
    doc["beats"] = beats
    process = str(doc.get("outcome_process") or "").strip()
    law = str(doc.get("law") or "").strip().rstrip(".")
    if len(process) >= 20 and law and not _outcome_linked(doc):
        doc["outcome_process"] = f"{law}, поэтому {process[0].lower()}{process[1:]}"
        fixes.append("outcome_linked_to_law")
    prev = None
    for b in beats:
        for f in b.get("frames") or []:
            if not isinstance(f, dict):
                continue
            raw = str(f.get("camera") or "").strip()
            cam = _canon_token(raw, cams)
            if cam not in cams:
                key = raw.casefold().replace("_", " ")
                cam = _CAMERA_ALIAS.get(key) or next((v for k, v in _CAMERA_ALIAS.items() if k in key), "mid")
                fixes.append("camera_repaired")
            if cam == "close" and prev == "close":
                cam = "mid"
                fixes.append("double_close_repaired")
            f["camera"] = cam
            prev = cam
            feel = _canon_token(str(f.get("camera_feel") or "").strip(), feels)
            if feel not in feels:
                low = feel.casefold()
                feel = next((x for x in feels if x.split("_")[1] in low), _FEEL_BY_BEAT.get(b["id"], "handheld_soft"))
                f["camera_feel"] = feel
                fixes.append("feel_repaired")
            else:
                f["camera_feel"] = feel
    return fixes


def validate_door_scenario(
    doc: dict[str, Any],
    *,
    other_theme: str | None = None,
    category: str | None = None,
    recent: list[dict[str, str]] | None = None,
    other_mechanic: str | None = None,
) -> list[str]:
    """Самопроверка двери. Визуализация собирается первой."""
    contract = load_v2("frame_contract.json")
    checks = load_v2("self_check.json")
    beat_ids = [str(item["id"]) for item in load_v2("beats.json")["beats"]]
    keys = list(contract["visual_change_keys"])
    blob = "\n".join(_strings(doc))
    errors = visualization_errors(blob)

    beats = doc.get("beats") if isinstance(doc.get("beats"), list) else []
    action_bad = False
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        frames = beat.get("frames") if isinstance(beat.get("frames"), list) else []
        for frame in frames:
            if not isinstance(frame, dict):
                continue
            action = str(frame.get("action") or "")
            if not _physical(action):
                action_bad = True
    if action_bad:
        errors.append("visual:action")

    errors.extend(stoplist_errors(blob))

    theme = str(doc.get("theme") or "").strip()
    norm_theme = _norm(theme)
    dull = {_norm(item) for item in checks["dull_themes"]}
    if len(norm_theme) < 12 or len(norm_theme.split()) < 3:
        errors.append("theme_weak")
    elif norm_theme in dull:
        errors.append("theme_dull")

    law = str(doc.get("law") or "").strip()
    enders = law.count(".") + law.count("!") + law.count("?")
    folded = _norm(law)
    many = enders > 1 or ";" in law or " а также " in f" {folded} " or " одновременно " in f" {folded} "
    if len(law) < 20:
        errors.append("law_missing")
    elif many:
        errors.append("law_many")

    outcome = _outcome(doc.get("outcome"))
    if outcome not in set(checks["outcomes"]):
        errors.append("outcome_invalid")
    process = str(doc.get("outcome_process") or "").strip()
    beat_text = " ".join(str(b.get("text") or "") for b in beats if isinstance(b, dict) and b.get("id") in ("law", "threat", "peak"))
    linked = {w[:4] for w in _stems(" ".join([law, theme, str(doc.get("mechanic") or ""), str(doc.get("twist") or ""), str(doc.get("icon") or ""), beat_text]))}
    linked -= {"геро", "кото", "кадр", "всех", "будт"}
    if len(process) < 20:
        errors.append("outcome_unlinked")
    peak_text = " ".join(str(b.get("text") or "") for b in beats if isinstance(b, dict) and b.get("id") in ("threat", "peak"))
    if outcome == "death" and len(str(doc.get("twist") or "")) < 15:
        errors.append("death_passive")
    if outcome == "escape":
        esc_text = " ".join(str(b.get("text") or "") for b in beats if isinstance(b, dict) and b.get("id") in ("peak", "outcome")).casefold()
        if _GENERIC_ESCAPE.search(esc_text):
            errors.append("escape_generic")
    if outcome == "transformation":
        final_form = " ".join([process, str(doc.get("final_form") or ""), " ".join(str(b.get("text") or "") for b in beats if isinstance(b, dict) and b.get("id") in ("outcome", "hook"))])
        if _NOT_SOLID.search(final_form.casefold()):
            errors.append("transform_not_solid")
    if recent and False:
        window = int(load_v2("categories.json").get("family_window", 6))
        mine = theme_families(" ".join([theme, law, str(doc.get("icon") or "")]))
        seen: set[str] = set()
        for item in [r for r in recent if not r.get("reserved")][-window:]:
            seen |= theme_families(" ".join(str(item.get(k) or "") for k in ("theme", "mechanic")))
        if mine & seen:
            errors.append("family_repeat:" + ",".join(sorted(mine & seen)))

    if len(beats) != len(beat_ids):
        errors.append("beats_count")

    prev_cam: str | None = None
    for index, beat_id in enumerate(beat_ids):
        beat = beats[index] if index < len(beats) and isinstance(beats[index], dict) else None
        if beat is None:
            continue
        if str(beat.get("id") or "") != beat_id:
            errors.append(f"beat_id:{beat_id}")
        frames = beat.get("frames") if isinstance(beat.get("frames"), list) else []
        if not 1 <= len(frames) <= 3:
            errors.append(f"frames_count:{beat_id}")
        if beat_id == "peak":
            text = str(beat.get("text") or "").strip()
            vivid = len(text) >= 40 and any(
                len(str(frame.get("action") or "").strip()) >= 15
                for frame in frames
                if isinstance(frame, dict)
            )
            if not vivid:
                errors.append("peak_weak")
        for frame in frames:
            if not isinstance(frame, dict):
                errors.append("frames_count:" + beat_id)
                continue
            start = str(frame.get("start_frame") or "").strip()
            end = str(frame.get("end_frame") or "").strip()
            if not start or not end or start == end:
                errors.append("no_change")
            changes = _changes(frame.get("visual_changes"), keys)
            frame["visual_changes"] = changes
            for key, value in changes.items():
                if not value:
                    errors.append(f"visual_changes:{key}")
            camera = _canon_token(frame.get("camera"), list(contract["cameras"]))
            feel = _canon_token(frame.get("camera_feel"), list(contract["feels"]))
            frame["camera"] = camera
            frame["camera_feel"] = feel
            if camera not in contract["cameras"]:
                errors.append("bad_camera")
            if feel not in contract["feels"]:
                errors.append("bad_camera_feel")
            if camera == "close" and prev_cam == "close":
                errors.append("double_close")
            prev_cam = camera

    if other_theme and themes_conflict(theme, other_theme):
        errors.append("themes_same")

    cid = str(doc.get("category") or "").strip()
    mechanic = str(doc.get("mechanic") or "").strip()
    if cid not in category_ids():
        errors.append("category_invalid")
    elif category and cid != category:
        errors.append("category_mismatch")
    if len(mechanic) < 6:
        errors.append("mechanic_missing")
    scores = doc.get("self_scores")
    if not isinstance(scores, dict) or not scores:
        errors.append("self_score_missing")
    else:
        low = []
        for key in ("emotion", "interest", "stakes", "concept"):
            try:
                if float(scores.get(key, 0)) < SELF_SCORE_FLOOR:
                    low.append(key)
            except (TypeError, ValueError):
                low.append(key)
        if low:
            errors.append("self_score_low:" + ",".join(low))
    attack_text = " ".join(
        [str(doc.get("outcome_process") or "")]
        + [str(b.get("text") or "") for b in beats if isinstance(b, dict) and b.get("id") in ("peak", "outcome")]
    )
    if creature_attack(attack_text):
        errors.append("creature_attack")
    twist = str(doc.get("twist") or "").strip()
    if len(twist) < 15:
        errors.append("twist_missing")
    else:
        peak_text = " ".join(
            [str(b.get("text") or "") for b in beats if isinstance(b, dict) and b.get("id") in ("peak", "outcome")]
        )
        if not ({w[:4] for w in _stems(twist)} & {w[:4] for w in _stems(peak_text)}):
            errors.append("twist_not_shown")
    if other_mechanic and mechanic and themes_conflict(mechanic, other_mechanic):
        errors.append("mechanic_same")
    icon_key = _norm(str(doc.get("icon") or ""))
    for item in (recent or [])[-20:]:
        if icon_key and icon_key == _norm(item.get("icon", "")):
            errors.append("theme_repeat")
            break
        if themes_conflict(theme, item.get("theme", "")) or (
            mechanic and themes_conflict(mechanic, item.get("mechanic", ""))
        ):
            errors.append("theme_repeat")
            break
    errors.extend(finale_line_errors(outcome, str(doc.get("finale_line") or "")))
    return errors


def parse_scenario_json(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("json") from None
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("object")
    return data


def build_system_prompt() -> str:
    stoplist_lines = ["СТОП-ЛИСТ. Эти темы и близкие копии запрещены:"]
    for item in load_v2("stoplist.json")["items"]:
        stoplist_lines.append(f"- {item['title']}. Близко: {item['close']}")
    beat_lines = ["ШЕСТЬ БИТОВ по порядку:"]
    for item in load_v2("beats.json")["beats"]:
        beat_lines.append(f"- {item['id']}: {item['title']}. {item['goal']}")
    cat_lines = ["КАТЕГОРИИ МИРОВ (id — название: подсказка):"]
    for item in categories():
        cat_lines.append(f"- {item['id']} — {item['title']}: {item['hint']}")
    cat_lines.append(str(load_v2("categories.json")["overused"]))
    check_lines = ["САМОПРОВЕРКА перед ответом. Если пункт не выполнен — перепиши ответ:"]
    labels = load_v2("self_check.json")["check_labels"]
    for key in load_v2("self_check.json")["checks"]:
        check_lines.append(f"- {labels[key]}")
    parts = [
        _read("agent_prompt.md").strip(),
        _read("world.md").strip(),
        _read("visualization.md").strip(),
        _read("storyboard.md").strip(),
        "\n".join(stoplist_lines),
        "\n".join(cat_lines),
        "\n".join(beat_lines),
        "\n".join(check_lines),
        "СХЕМА JSON:\n" + json.dumps(load_v2("output_schema.json"), ensure_ascii=False, indent=2),
        "КОНТРАКТ КАДРА:\n" + json.dumps(load_v2("frame_contract.json"), ensure_ascii=False),
        "КАМЕРА:\n" + json.dumps(load_v2("camera.json"), ensure_ascii=False),
    ]
    return "\n\n".join(parts)


def _user_prompt(
    door_id: str,
    *,
    gg: str,
    video_name: str,
    errors: list[str],
    avoid_theme: str | None,
    category: str | None = None,
    recent: list[dict[str, str]] | None = None,
    avoid_mechanic: str | None = None,
    avoid_outcome: str | None = None,
    outcome: str | None = None,
    candidates: list[str] | None = None,
    plan_text: str | None = None,
) -> str:
    side = "левая" if door_id == "door1" else "правая"
    lines = [
        f"Собери полный сценарий {side} двери ({door_id}).",
        f"Герой: {gg}.",
        "Исходное видео: " + (video_name or "ещё не загружено, на мир двери не влияет") + ".",
        "Пиши только эту дверь. Интро и вторую дверь не добавляй.",
    ]
    if category:
        cat = _category(category)
        lines.append(f"Категория мира строго: {cat['id']} — {cat['title']} ({cat['hint']}). В поле category верни \"{cat['id']}\".")
    recent = [item for item in (recent or []) if item.get("theme") != EXAMPLE_THEME["theme"]]
    if recent:
        lines.append("Недавние миры, их и их механики не повторяй:")
        for item in recent[-30:]:
            lines.append(f"- {item['theme']} / {item['mechanic']}")
    if plan_text:
        lines.append("УТВЕРЖДЁННЫЙ ПЛАН ДВЕРИ. Строго раскадруй именно его: мир, события и исход не меняй и не придумывай новые, только детализируй по правилам (биты, кадры, ракурсы, ручная камера, визуальные изменения):")
        lines.append(plan_text)
    if candidates:
        lines.append("Кандидаты (выбери ровно один, icon = он): " + "; ".join(candidates) + ".")
    if outcome:
        lines.append(f"Исход строго: {outcome}.")
    if avoid_outcome:
        lines.append("Другая дверь уже закончилась так: " + avoid_outcome + ". Выбери ДРУГОЙ исход и другую эмоцию, если это логично для места.")
    if avoid_mechanic:
        lines.append("Ядро механики другой двери, не повторяй его: " + avoid_mechanic)
    if avoid_theme:
        lines.append("Мир другой двери уже занят, не повторяй его и близкие формулировки: " + avoid_theme)
    if errors:
        lines.append("Прошлая попытка отклонена. Сначала убери всё, что нельзя увидеть глазом:")
        for code in errors:
            lines.append(f"- {code}: {_human(code)}")
    lines.append("Верни один JSON-объект и ничего кроме него.")
    return "\n".join(lines)


def _refs(gg: str, world: str, extra: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    refs = [
        {"type": "style", "name": "nii67_style", "attached": False},
        {"type": "character", "name": gg, "attached": False},
        {"type": "location", "name": world or "мир двери", "attached": False},
    ]
    return list(extra or []) + refs


def _scene(goal: str, law: str, action: str, changes: dict[str, str], start: str, end: str, hook: str) -> dict[str, str]:
    return {
        "goal": (goal or action or "видимая цель кадра")[:300],
        "obstacle": (law or "закон мира мешает")[:300],
        "action": action or "герой делает видимый шаг",
        "reaction": changes.get("environment") or changes.get("body") or "окружение в кадре меняется",
        "change": f"{start} → {end}",
        "hook": hook or "что видно дальше",
    }


def _chars(gg: str, raw: Any) -> list[str]:
    chars: list[str] = []
    for item in [gg, *(raw if isinstance(raw, list) else [])]:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if not name or name in chars or name == "ГГ":
            continue
        if name == "Герой" and gg != "Герой":
            continue
        chars.append(name)
    return chars or [gg]


def _frame(
    *,
    slot_id: str,
    door: str | None,
    beat: str,
    start: str,
    end: str,
    camera: str,
    feel: str,
    action: str,
    gg: str,
    world: str,
    law: str,
    goal: str,
    hook: str,
    changes: dict[str, str],
    characters: Any = None,
    extra_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "slot_id": slot_id,
        "door": door,
        "beat": beat,
        "start_frame": start,
        "end_frame": end,
        "camera": camera,
        "camera_feel": feel,
        "characters_on": _chars(gg, characters),
        "action": action,
        "refs": _refs(gg, world, extra_refs),
        "duration_hint": 0.0,
        "visual_changes": changes,
        "scene": _scene(goal, law, action, changes, start, end, hook),
    }


def _shared_changes(pose: str, light: str, body: str, scale: str, environment: str) -> dict[str, str]:
    return {"pose": pose, "light": light, "body": body, "scale": scale, "environment": environment}


def build_frames(card: dict[str, Any], door_docs: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    gg = str(card["gg"]["name"] or "").strip() or "Герой"
    video = str(card["intro"].get("video_name") or "").strip()
    portal = card["portal"]
    titles = {item["id"]: item["title"] for item in load_v2("beats.json")["beats"]}
    frames: list[dict[str, Any]] = []
    frames.append(
        _frame(
            slot_id="intro",
            door=None,
            beat="Исходное видео",
            start=f"{gg} стоит в комнате перед двумя закрытыми дверями",
            end=f"{gg} протягивает руку к ручке одной двери, вторая дверь закрыта",
            camera="mid",
            feel="handheld_soft",
            action=f"{gg} шагает к дверям и тянет руку к ручке",
            gg=gg,
            world="комната",
            law="две закрытые двери",
            goal=f"{gg} подходит к выбору двери",
            hook="какая дверь откроется",
            changes=_shared_changes(
                "рука поднимается к ручке",
                "лампа комнаты светит ровно",
                "ладонь открыта",
                "герой в полный рост",
                "две закрытые двери впереди",
            ),
            extra_refs=[{
                "type": "motion",
                "role": "source_video",
                "name": video or "исходное видео",
                "attached": bool(video),
            }],
        )
    )
    left = portal.get("left_design") or "левая дверь"
    right = portal.get("right_design") or "правая дверь"
    frames.append(
        _frame(
            slot_id="doors",
            door=None,
            beat="Двери",
            start="Две обычные закрытые двери стоят рядом",
            end=f"Левая дверь стала такой: {left}. Правая дверь стала такой: {right}",
            camera="far",
            feel="handheld_soft",
            action=(
                f"Левая дверь меняется: {portal.get('left_anim') or left}. "
                f"Правая дверь меняется: {portal.get('right_anim') or right}"
            ),
            gg=gg,
            world="две двери",
            law="каждая дверь показывает свой закон",
            goal="показать превращение обеих дверей",
            hook="какую дверь откроет герой",
            changes=_shared_changes(
                "двери остаются закрытыми",
                "свет лампы падает на обе створки",
                "створки занимают кадр",
                "обе двери целиком",
                "комната и две преображённые створки",
            ),
            extra_refs=[
                {
                    "type": "location",
                    "role": "door_image",
                    "name": portal.get("image_name") or "фото дверей",
                    "attached": bool(portal.get("image_name")),
                },
                {
                    "type": "motion",
                    "role": "transformation_video",
                    "name": portal.get("video_name") or "видео трансформации",
                    "attached": bool(portal.get("video_name")),
                },
            ],
        )
    )

    for door_id in ("door1", "door2"):
        doc = door_docs[door_id]
        world = str(doc.get("theme") or "")
        law = str(doc.get("law") or "")
        for beat in doc["beats"]:
            beat_id = str(beat["id"])
            title = titles.get(beat_id, beat_id)
            for index, src in enumerate(beat["frames"]):
                extra = None
                if beat_id == "hook":
                    extra = [{
                        "type": "style",
                        "role": "illustration",
                        "name": "иллюстрация по описанию",
                        "attached": False,
                    }]
                frames.append(
                    _frame(
                        slot_id=f"{door_id}.{beat_id}.{index}",
                        door=door_id,
                        beat=title,
                        start=str(src.get("start_frame") or "").strip(),
                        end=str(src.get("end_frame") or "").strip(),
                        camera=str(src.get("camera") or "mid"),
                        feel=str(src.get("camera_feel") or "handheld_soft"),
                        action=str(src.get("action") or "").strip(),
                        gg=gg,
                        world=world,
                        law=law,
                        goal=str(beat.get("text") or title),
                        hook=str(doc.get("illustration") or ""),
                        changes=dict(src.get("visual_changes") or {}),
                        characters=src.get("characters_on"),
                        extra_refs=extra,
                    )
                )
    finale = str(card["finale"].get("standard") or "").strip()
    fin_start, fin_action, fin_body = _finale_action(gg, door_docs)
    frames.append(
        _frame(
            slot_id="finale",
            door=None,
            beat="Общий финал",
            start=fin_start,
            end=finale or fin_action,
            camera="far",
            feel="handheld_soft",
            action=fin_action,
            gg=gg,
            world="комната",
            law="обе двери уже закрыты",
            goal="свести обе двери в одной комнате",
            hook="чем закончилась каждая ветка",
            changes=_shared_changes(
                "по исходу каждой двери",
                "обычный свет лампы",
                fin_body,
                "комната и обе двери целиком",
                "комната после обеих дверей",
            ),
        )
    )
    return frames


def _make_llm(project: Any) -> LlmFn:
    from app.services.gpt_client import require_gpt_api

    require_gpt_api()

    async def _call(prompt: str, *, system: str, door_id: str) -> str:
        from app.services.gpt_api import chat
        from app.services.llm_override import bind_generation_llm

        with bind_generation_llm(project, node_type="script"):
            result = await chat(
                prompt=prompt,
                system=system,
                auto_pack=False,
                timeout=150,
                max_retries=1,
            )
        logger.info("nii67 scenario: {} model={} chars={}", door_id, result.model, len(result.text or ""))
        return result.text or ""

    return _call


async def _generate_door(
    door_id: str,
    *,
    llm: LlmFn,
    system: str,
    gg: str,
    video_name: str,
    max_attempts: int,
    other_theme: str | None = None,
    category: str | None = None,
    recent: list[dict[str, str]] | None = None,
    other_mechanic: str | None = None,
    other_outcome: str | None = None,
    outcome: str | None = None,
    candidates: list[str] | None = None,
    judge: bool = False,
    plan_text: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    log: list[dict[str, Any]] = []
    errors: list[str] = []
    for attempt in range(1, max_attempts + 1):
        prompt = _user_prompt(
            door_id,
            gg=gg,
            video_name=video_name,
            errors=errors,
            avoid_theme=other_theme,
            category=category,
            recent=recent,
            avoid_mechanic=other_mechanic,
            avoid_outcome=other_outcome,
            outcome=outcome,
            candidates=candidates,
            plan_text=plan_text,
        )
        raw = None
        for net_try in range(PROVIDER_TRIES):
            try:
                raw = await llm(prompt, system=system, door_id=door_id)
                break
            except AssertionError:
                raise
            except Exception as exc:
                logger.warning(
                    "nii67 scenario: {} attempt {} provider try {} failed ({})",
                    door_id, attempt, net_try + 1, type(exc).__name__,
                )
                await asyncio.sleep(PROVIDER_RETRY_S * (2 ** net_try))
        if raw is None:
            errors = ["llm_error"]
            log.append({"door": door_id, "attempt": attempt, "errors": errors})
            continue
        try:
            doc = parse_scenario_json(raw)
        except (ValueError, json.JSONDecodeError):
            errors = ["json_invalid"]
            log.append({"door": door_id, "attempt": attempt, "errors": errors})
            continue
        if plan_text:
            if str(doc.get("category") or "").strip() not in category_ids():
                doc["category"] = category or category_ids()[0]
        fixes = repair_door_doc(doc)
        if fixes:
            logger.info("nii67 scenario: {} attempt {} auto-repaired {}", door_id, attempt, fixes)
        errors = validate_door_scenario(
            doc,
            other_theme=other_theme,
            category=category,
            recent=recent,
            other_mechanic=other_mechanic,
        )
        if candidates:
            want = {_norm(c): c for c in candidates}
            got = _norm(str(doc.get("icon") or ""))
            match = want.get(got) or next((c for k, c in want.items() if got and (got in k or k in got)), None)
            if match:
                doc["icon"] = match
            else:
                errors = list(errors) + ["icon_not_candidate"]
        if outcome and _outcome(doc.get("outcome")) != outcome:
            errors = list(errors) + ["outcome_mismatch"]
        if judge and not errors:
            try:
                verdict = await judge_door(llm, doc, door_id)
                doc["family"] = verdict["family"]
                errors = judge_errors(verdict, _outcome(doc.get("outcome")), recent)
                logger.info("nii67 scenario: {} attempt {} judge {}", door_id, attempt, verdict)
            except Exception as exc:  # судья не обязателен
                logger.warning("nii67 scenario: {} judge failed ({})", door_id, type(exc).__name__)
        if other_outcome and not errors:
            same_out = _outcome(doc.get("outcome")) == _outcome(other_outcome.split("/")[0])
            if same_out:
                errors = ["doors_same_arc"]
        if errors:
            log.append({
                "door": door_id,
                "attempt": attempt,
                "errors": errors,
                "theme": str(doc.get("theme") or "")[:160],
                "detail": {
                    "law": str(doc.get("law") or "")[:200],
                    "outcome": str(doc.get("outcome") or ""),
                    "outcome_process": str(doc.get("outcome_process") or "")[:200],
                    "finale_line": str(doc.get("finale_line") or "")[:200],
                },
            })
            logger.info(
                "nii67 scenario: {} attempt {} rejected {} | {} | outcome={} | process={} | finale={}",
                door_id, attempt, errors, doc.get("theme"), doc.get("outcome"),
                doc.get("outcome_process"), doc.get("finale_line"),
            )
            continue
        doc["outcome"] = _outcome(doc.get("outcome"))
        return doc, log
    raise ScenarioGenerationError(door_id, errors, attempts=max_attempts)


async def generate_scenarios(
    card: dict[str, Any],
    *,
    llm: LlmFn | None = None,
    sleep: SleepFn | None = None,
    project: Any = None,
    stagger_s: float | None = None,
    max_attempts: int | None = None,
    categories_pick: list[str] | None = None,
    outcomes_pick: list[str] | None = None,
    history_path: Path | None = None,
    plan: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Раскадровка обеих дверей. С планом — строго по тексту плана, без новых миров."""
    from app.services.nii67_agent import (
        assign_durations,
        camera_errors,
        fix_camera_sequence,
        load_template,
        validate_frame,
        validate_scene,
    )
    from app.services.nii67_card import apply_generated_scenario

    cfg = load_v2("self_check.json")
    pause = float(cfg["stagger_s"] if stagger_s is None else stagger_s)
    attempts = int(cfg["max_attempts"] if max_attempts is None else max_attempts)
    sleeper = sleep or asyncio.sleep
    judge_on = llm is None and not plan
    caller = llm or _make_llm(project)
    system = build_system_prompt()
    gg = str((card.get("gg") or {}).get("name") or "").strip() or "Герой"
    video = str((card.get("intro") or {}).get("video_name") or "").strip()
    recent = recent_worlds(history_path) + [dict(EXAMPLE_THEME)]
    plan_texts: dict[str, str] = {}
    if plan:
        valid = set(category_ids())
        cat_of = {d: (str(plan[d].get("category") or "") if str(plan[d].get("category") or "") in valid else None) for d in ("door1", "door2")}
        roles = {"door1": "Левая дверь", "door2": "Правая дверь"}
        for d in ("door1", "door2"):
            plan_texts[d] = (
                f"{roles[d]}. Мир: {plan[d].get('world', '').strip()}\n"
                f"Что происходит и пик: {plan[d].get('happens', '').strip()}\n"
                f"Исход: {plan[d].get('outcome') or 'реши сам строго по описанию'}"
            )
        recent = []
        out1 = plan["door1"].get("outcome") or None
        out2 = plan["door2"].get("outcome") or None
        plan_pick = {"door1": (out1, []), "door2": (out2, [])}
    else:
        cats = list(categories_pick or pick_categories(2, path=history_path))
        cat_of = {"door1": cats[0], "door2": cats[1]}
        out1, cand1 = plan_door(cats[0], recent)
        out2, cand2 = plan_door(cats[1], recent, avoid_outcome=out1)
        if outcomes_pick:
            out1, out2 = outcomes_pick[0], outcomes_pick[1]
            cand1, cand2 = [], []
        plan_pick = {"door1": (out1, cand1), "door2": (out2, cand2)}
    logger.info("nii67 scenario: plan {}", plan_pick)

    async def _run(door_id: str, delay: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if delay > 0:
            await sleeper(delay)
        return await _generate_door(
            door_id,
            llm=caller,
            judge=judge_on,
            system=system,
            gg=gg,
            video_name=video,
            max_attempts=attempts,
            category=cat_of[door_id],
            recent=recent,
            outcome=plan_pick[door_id][0],
            candidates=plan_pick[door_id][1],
            plan_text=plan_texts.get(door_id),
        )

    results = await asyncio.gather(_run("door1", 0), _run("door2", pause), return_exceptions=True)
    for item in results:
        if isinstance(item, Exception):
            raise item
    (doc1, log1), (doc2, log2) = results
    logs = list(log1) + list(log2)
    same_arc = _outcome(doc1.get("outcome")) == _outcome(doc2.get("outcome"))
    if plan:
        same_arc = False
    if not plan and (same_arc or themes_conflict(str(doc1.get("theme") or ""), str(doc2.get("theme") or "")) or themes_conflict(
        str(doc1.get("mechanic") or ""), str(doc2.get("mechanic") or "")
    )):
        try:
            doc2, more = await _generate_door(
                "door2",
                llm=caller,
            judge=judge_on,
                system=system,
                gg=gg,
                video_name=video,
                max_attempts=attempts,
                other_theme=str(doc1.get("theme") or ""),
                category=cat_of["door2"],
                recent=recent,
                other_mechanic=str(doc1.get("mechanic") or ""),
                other_outcome=f"{_outcome(doc1.get('outcome'))}/{str(doc1.get('emotion') or '').strip()}",
                outcome=plan_pick["door2"][0],
                candidates=plan_pick["door2"][1],
            )
            logs.extend(more)
        except ScenarioGenerationError as exc:
            if not same_arc:
                raise
            logs.append({"door": "door2", "attempt": 0, "errors": ["doors_same_arc_kept", *exc.errors]})

    finale = build_finale(gg, doc1, doc2)
    record_history([doc1, doc2], path=history_path)
    filled = apply_generated_scenario(card, {"door1": doc1, "door2": doc2}, finale)
    if plan and str(plan.get("finale") or "").strip():
        filled.setdefault("finale", {})["notes"] = str(plan["finale"]).strip()[:2000]
    if plan:
        filled["plan"] = plan
    frames = fix_camera_sequence(build_frames(filled, {"door1": doc1, "door2": doc2}))
    assign_durations(frames)
    contract_errors = [err for frame in frames for err in validate_frame(frame) + validate_scene(frame)]
    contract_errors.extend(camera_errors(frames))
    if contract_errors:
        raise ScenarioGenerationError("frames", ["frame_contract", *contract_errors[:6]], attempts=attempts)
    fmt = load_template("slots")["format"]
    return filled, {
        "template_version": TEMPLATE_VERSION,
        "format": fmt,
        "frames": frames,
        "rewrite_log": logs,
        "errors": [],
    }


PLAN_SYSTEM = (
    "Ты автор коротких роликов NII 67: герой открывает дверь и попадает в целую узнаваемую реальность. "
    "Составь КОРОТКИЙ план одной двери по правилам мира ниже. Ответ — только JSON: "
    '{"world":"<узнаваемый мир, 1 строка>","happens":"<2-4 предложения: что видит, как мир действует на тело, '
    'поворот, пик, что делает герой>","outcome":"death|escape|transformation"}.\n\n'
)


async def generate_plan(
    card: dict[str, Any],
    *,
    llm: LlmFn | None = None,
    project: Any = None,
    categories_pick: list[str] | None = None,
    outcomes_pick: list[str] | None = None,
    history_path: Path | None = None,
) -> dict[str, Any]:
    """Этап 1: короткий редактируемый план двух дверей + финал (по одному вызову модели на дверь)."""
    from app.services.nii67_card import normalize_plan

    caller = llm or _make_llm(project)
    system = PLAN_SYSTEM + build_system_prompt()
    gg = str((card.get("gg") or {}).get("name") or "").strip() or "Герой"
    recent = recent_worlds(history_path)
    cats = list(categories_pick or pick_categories(2, path=history_path))
    out1, cand1 = plan_door(cats[0], recent)
    out2, cand2 = plan_door(cats[1], recent, avoid_outcome=out1)
    if outcomes_pick:
        out1, out2 = outcomes_pick[0], outcomes_pick[1]
    picks = {"door1": (cats[0], out1, cand1), "door2": (cats[1], out2, cand2)}

    async def _one(door_id: str) -> dict[str, str]:
        cid, outcome, cands = picks[door_id]
        cat = _category(cid)
        lines = [
            f"План {'левой' if door_id == 'door1' else 'правой'} двери. Герой: {gg}.",
            f"Категория мира: {cat['title']} ({cat['hint']}).",
            f"Исход строго: {outcome}.",
        ]
        if cands:
            lines.append("Возьми один из миров: " + "; ".join(cands) + ".")
        if recent:
            lines.append("Не повторяй недавние миры: " + "; ".join(r["theme"] for r in recent[-15:] if r.get("theme")))
        lines.append("Верни один JSON-объект.")
        last: Exception | None = None
        for _ in range(2):
            try:
                doc = parse_scenario_json(await caller("\n".join(lines), system=system, door_id=door_id + "-plan"))
            except Exception as exc:
                last = exc
                continue
            world = str(doc.get("world") or "").strip()
            happens = str(doc.get("happens") or "").strip()
            if world and len(happens) >= 20:
                return {"world": world, "happens": happens, "outcome": outcome, "category": cid}
        raise ScenarioGenerationError(door_id, ["plan_invalid" if last is None else "llm_error"], attempts=2)

    d1, d2 = await asyncio.gather(_one("door1"), _one("door2"))
    names = {"death": "герой не вернулся", "escape": "герой спасся и вернулся в комнату", "transformation": "герой вернулся изменённым"}
    finale = (
        f"Левая дверь — {names[d1['outcome']]}. Правая дверь — {names[d2['outcome']]}. "
        "Финальная иллюстрация показывает, что стало с героем в каждой ветке."
    )
    return normalize_plan({"door1": d1, "door2": d2, "finale": finale})


__all__ = [
    "generate_plan",
    "ScenarioGenerationError",
    "build_finale",
    "build_system_prompt",
    "finale_line_errors",
    "has_verb",
    "nonvisual_verbs",
    "pick_categories",
    "generate_scenarios",
    "parse_scenario_json",
    "stoplist_errors",
    "themes_conflict",
    "validate_door_scenario",
    "visualization_errors",
]
