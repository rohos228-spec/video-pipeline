"""Автосценарий NII 67: мир, закон, шесть битов и раскадровка.

Правила лежат в ``prompts/templates/nii67/v2``. На дверь — один вызов текстовой
модели с маршрута ноды «Сценарий» (vibecode, по умолчанию gpt-5.6-sol).
Двери стартуют параллельно, вторая с паузой из шаблона. Поля карточки
заполняются и остаются редактируемыми. Оператор загружает исходное видео.
"""

from __future__ import annotations

import asyncio
import json
import re
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
    }
    if code in fixed:
        return fixed[code]
    if code.startswith("beat_id"):
        return "бит не на своём месте"
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


def _physical(action: str) -> bool:
    hay = _scan(action)
    return any(stem in hay for stem in load_v2("visualization.json")["physical_verbs"])


def validate_door_scenario(doc: dict[str, Any], *, other_theme: str | None = None) -> list[str]:
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
    if len(process) < 20 or not (_stems(law) & _stems(process)):
        errors.append("outcome_unlinked")

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
) -> str:
    side = "левая" if door_id == "door1" else "правая"
    lines = [
        f"Собери полный сценарий {side} двери ({door_id}).",
        f"Герой: {gg}.",
        "Исходное видео: " + (video_name or "ещё не загружено, на мир двери не влияет") + ".",
        "Пиши только эту дверь. Интро и вторую дверь не добавляй.",
    ]
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
    frames.append(
        _frame(
            slot_id="finale",
            door=None,
            beat="Общий финал",
            start=f"{gg} выходит из проёма обратно в комнату",
            end=finale or f"{gg} стоит в комнате, на нём видны следы обеих дверей",
            camera="far",
            feel="handheld_soft",
            action=f"{gg} стоит в комнате, следы обеих дверей видны на теле и одежде",
            gg=gg,
            world="комната",
            law="обе двери уже закрыты",
            goal="свести обе двери в одной комнате",
            hook="какой след остался на герое",
            changes=_shared_changes(
                "герой стоит лицом к комнате",
                "обычный свет лампы",
                "на теле видны следы обеих дверей",
                "герой целиком в комнате",
                "комната без открытых дверей",
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
        )
        try:
            raw = await llm(prompt, system=system, door_id=door_id)
        except Exception as exc:
            logger.warning("nii67 scenario: {} attempt {} failed ({})", door_id, attempt, type(exc).__name__)
            errors = ["llm_error"]
            log.append({"door": door_id, "attempt": attempt, "errors": errors})
            continue
        try:
            doc = parse_scenario_json(raw)
        except (ValueError, json.JSONDecodeError):
            errors = ["json_invalid"]
            log.append({"door": door_id, "attempt": attempt, "errors": errors})
            continue
        errors = validate_door_scenario(doc, other_theme=other_theme)
        if errors:
            log.append({"door": door_id, "attempt": attempt, "errors": errors})
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
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Сгенерировать обе двери и собрать карточку с кадрами."""
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
    caller = llm or _make_llm(project)
    system = build_system_prompt()
    gg = str((card.get("gg") or {}).get("name") or "").strip() or "Герой"
    video = str((card.get("intro") or {}).get("video_name") or "").strip()

    async def _run(door_id: str, delay: float) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if delay > 0:
            await sleeper(delay)
        return await _generate_door(
            door_id,
            llm=caller,
            system=system,
            gg=gg,
            video_name=video,
            max_attempts=attempts,
        )

    results = await asyncio.gather(_run("door1", 0), _run("door2", pause), return_exceptions=True)
    for item in results:
        if isinstance(item, Exception):
            raise item
    (doc1, log1), (doc2, log2) = results
    logs = list(log1) + list(log2)
    if themes_conflict(str(doc1.get("theme") or ""), str(doc2.get("theme") or "")):
        doc2, more = await _generate_door(
            "door2",
            llm=caller,
            system=system,
            gg=gg,
            video_name=video,
            max_attempts=attempts,
            other_theme=str(doc1.get("theme") or ""),
        )
        logs.extend(more)

    finale = {
        "standard": (
            f"{gg} снова стоит в комнате. Слева видно: {str(doc1.get('finale_line') or '').strip()} "
            f"Справа видно: {str(doc2.get('finale_line') or '').strip()}"
        ),
        "notes": (
            f"Левая дверь — {doc1.get('theme')}. Закон: {doc1.get('law')} "
            f"Правая дверь — {doc2.get('theme')}. Закон: {doc2.get('law')}"
        ),
    }
    filled = apply_generated_scenario(card, {"door1": doc1, "door2": doc2}, finale)
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


__all__ = [
    "ScenarioGenerationError",
    "build_system_prompt",
    "generate_scenarios",
    "parse_scenario_json",
    "stoplist_errors",
    "themes_conflict",
    "validate_door_scenario",
    "visualization_errors",
]
