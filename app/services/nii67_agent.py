"""Агент сценария NII 67: карточка → слоты → скелет → кадры (start/end).

Детерминированный: следует шаблонам из ``prompts/templates/nii67/v1``.
LLM можно подключить через ``rewrite`` (callable), иначе — автоправка по шаблону.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

from app.services.nii67_card import KIND_LABEL, normalize_card

TEMPLATE_VERSION = "v1"
TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "prompts" / "templates" / "nii67" / TEMPLATE_VERSION
SCENE_KEYS = ("goal", "obstacle", "action", "reaction", "change", "hook")
MAX_REWRITE = 3


@lru_cache(maxsize=None)
def load_template(name: str) -> dict[str, Any]:
    return json.loads((TEMPLATE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _cameras() -> list[str]:
    return load_template("camera")["cameras"]


def _feels() -> list[str]:
    return load_template("camera")["feels"]


# ---------- 1. Парсинг в слоты ----------

def _creature(block: dict[str, Any], door: dict[str, Any]) -> dict[str, Any] | None:
    for b in [block, *door["blocks"]]:
        ch = b.get("character") or {}
        if ch.get("enabled"):
            return ch
    return None


def parse_slots(card: Any) -> list[dict[str, Any]]:
    """Интро (исходное видео) → Двери (1 фото + 1 видео) → Door1|Door2 → иллюстрации → финал."""
    c = normalize_card(card)
    gg = c["gg"]["name"] or "ГГ"
    portal = c["portal"]
    slots: list[dict[str, Any]] = [{"slot_id": "intro", "type": "intro", "door": None, "text": "",
                                    "gg": gg, "world": "исходное видео", "creature": None,
                                    "video_name": c["intro"]["video_name"]}]
    slots.append({"slot_id": "doors", "type": "doors", "door": None, "gg": gg, "world": "две двери",
                  "creature": None, "text": "", "portal": portal,
                  "image_name": portal["image_name"], "video_name": portal["video_name"]})
    creature_kinds = set(load_template("slots")["creature_kinds"])
    sides = {"door1": ("left_design", "left_anim"), "door2": ("right_design", "right_anim")}
    for door in c["doors"]:
        design_key, _anim = sides[door["id"]]
        world = portal[design_key] or door["title"]
        for i, b in enumerate(door["blocks"]):
            ch = _creature(b, door)
            if b["kind"] in creature_kinds and ch is None:
                continue  # существо только если тогл включён
            base = {"gg": gg, "world": world, "door": door["id"],
                    "creature": (ch or {}).get("name") or ("существо" if ch else None),
                    "image_name": b["image_name"]}
            if b["kind"] == "exit":
                what = b["text"] or " ".join(
                    x for x in (b["exit_action"], b["exit_animation"], b["room_after"]) if x)
                slots.append({**base, "slot_id": f"{door['id']}.{i}.illustration",
                              "type": "illustration", "text": what})
                continue
            slots.append({**base, "slot_id": f"{door['id']}.{i}.{b['kind']}", "type": b["kind"],
                          "text": b["text"]})
    slots.append({"slot_id": "finale", "type": "finale", "door": None,
                  "text": " ".join(x for x in (c["finale"]["standard"], c["finale"]["notes"]) if x),
                  "gg": gg, "world": "комната", "creature": None})
    return slots


# ---------- 2. Скелет и кадры ----------

def _interaction_for(slot: dict[str, Any]) -> str:
    if slot["type"] in ("intro", "doors"):
        return "entry"
    if slot["type"] in ("finale", "illustration"):
        return "exit"
    key = load_template("slots")["kind_to_interaction"].get(slot["type"], "gift")
    if slot["creature"] is None and key in ("creature_reaction", "punchline", "gift"):
        key = "gg_action"
    return key


def _fill(tpl: str, slot: dict[str, Any]) -> str:
    return tpl.format(gg=slot["gg"], world=slot["world"], creature=slot["creature"] or "мир")


def build_scene(slot: dict[str, Any]) -> dict[str, Any]:
    key = _interaction_for(slot)
    t = load_template("interactions")["interactions"][key]
    scene = {k: _fill(t[k], slot) for k in SCENE_KEYS}
    if slot["text"]:  # вплетаем текст оператора в действие, а не приклеиваем
        scene["action"] = weave(slot["text"], slot)
    if slot["type"] == "intro":
        scene.update(goal=f"{slot['gg']} выбирает одну из двух дверей", obstacle="за обеими — неизвестность",
                     change="пустая комната → рука на ручке двери", hook="какую откроет?")
        scene["action"] = f"{slot['gg']} в исходном видео подходит к двум дверям"
    if slot["type"] == "doors":
        p = slot["portal"]
        scene.update(goal=f"{slot['gg']} должен выбрать одну из двух дверей",
                     obstacle="двери оживают и меняют облик",
                     action=f"Левая: {p['left_design'] or 'дверь 1'}; правая: {p['right_design'] or 'дверь 2'}",
                     reaction=f"Трансформация: {p['left_anim'] or 'левая оживает'} / {p['right_anim'] or 'правая оживает'}",
                     change="две обычные двери → две преображённые двери", hook="какую выберет?")
    if slot["type"] == "illustration":
        what = slot["text"] or f"что стало с {slot['gg']} в мире «{slot['world']}»"
        scene.update(goal=f"показать, что произошло с {slot['gg']}", obstacle="исход неизвестен",
                     action=f"Иллюстрация: {what}", reaction="мир замирает в итоговой картине",
                     change=f"{slot['gg']} в пути → итог: {what}", hook="а что было за другой дверью?")
    if slot["type"] == "finale":
        scene.update(goal="свести обе ветви", change="два мира → одна комната с трофеями обоих",
                     hook="подпишись, чтобы выбрать следующую дверь")
    a, _, b = scene["change"].partition("→")
    a = f"{a.strip()}; {slot['world']}, {slot['gg']} в начале движения"
    b = f"{b.strip()}; поза и свет уже другие, след действия виден в кадре"
    camera = load_template("slots")["kind_to_camera"].get(slot["type"], t["camera"])
    if slot["type"] in ("intro", "finale", "doors", "illustration"):
        camera = {"intro": "mid", "doors": "far", "illustration": "far", "finale": "far"}[slot["type"]]
    chars = [slot["gg"]] + ([slot["creature"]] if slot["creature"] else [])
    refs: list[dict[str, Any]] = []
    if slot["type"] == "intro":
        refs.append({"type": "motion", "role": "source_video", "name": slot.get("video_name") or "исходное видео",
                     "attached": bool(slot.get("video_name"))})
    if slot["type"] == "doors":
        refs.append({"type": "location", "role": "door_image", "name": slot.get("image_name") or "фото дверей",
                     "attached": bool(slot.get("image_name"))})
        refs.append({"type": "motion", "role": "transformation_video",
                     "name": slot.get("video_name") or "видео трансформации",
                     "attached": bool(slot.get("video_name"))})
    if slot["type"] == "illustration":
        refs.append({"type": "style", "role": "illustration", "name": "иллюстрация по описанию",
                     "attached": False})
    refs += [{"type": "style", "name": "nii67_style", "attached": False},
            {"type": "character", "name": slot["gg"], "attached": False},
            {"type": "location", "name": slot["world"], "attached": bool(slot.get("image_name"))}]
    if slot["creature"]:
        refs.append({"type": "character", "name": slot["creature"], "attached": False})
    if key in ("escape", "gg_action"):
        refs.append({"type": "motion", "name": key, "attached": False})
    return {"slot_id": slot["slot_id"], "door": slot["door"], "interaction": key,
            "beat": scene["goal"], "scene": scene,
            "start_frame": a.strip() or scene["action"], "end_frame": b.strip(),
            "camera": camera, "camera_feel": feel_for(camera, key, slot["type"]),
            "characters_on": chars, "action": scene["action"], "refs": refs, "duration_hint": 0.0}


def weave(text: str, slot: dict[str, Any]) -> str:
    t = text.strip().rstrip(".")
    if not t:
        return t
    subj = slot["gg"]
    if t[0].islower() and not t.lower().startswith(subj.lower()):
        t = f"{subj} {t}"
    if slot.get("creature") and slot["creature"] not in t and slot["type"] in ("world_character", "character_action"):
        t = f"{t} — {slot['creature']} рядом"
    return t[0].upper() + t[1:]


def feel_for(camera: str, interaction: str, slot_type: str) -> str:
    if slot_type in ("intro", "finale", "doors", "illustration") or camera == "far":
        return "handheld_soft"
    if interaction in ("escape", "gg_action"):
        return "handheld_action"
    if camera == "close":
        return "handheld_intimate"
    return "handheld_soft"


# ---------- 3. Правила ----------

def validate_frame(f: dict[str, Any]) -> list[str]:
    errs = [f"missing:{k}" for k in load_template("frame_contract")["required"] if k not in f]
    if f.get("camera") not in _cameras():
        errs.append("bad_camera")
    if f.get("camera_feel") not in _feels():
        errs.append("bad_camera_feel")
    for r in f.get("refs") or []:
        if r.get("type") not in load_template("frame_contract")["ref_types"] or not isinstance(r.get("attached"), bool):
            errs.append("bad_ref")
    return errs


def validate_scene(f: dict[str, Any]) -> list[str]:
    s = f.get("scene") or {}
    errs = [f"empty:{k}" for k in SCENE_KEYS if not str(s.get(k, "")).strip()]
    if not f.get("end_frame") or str(f.get("start_frame")).strip() == str(f.get("end_frame")).strip():
        errs.append("no_change")
    return errs


def fix_camera_sequence(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Нельзя два close подряд (в пределах одной ветви/таймлайна)."""
    prev: dict[str | None, str] = {}
    for f in frames:
        lane = f.get("door")
        if f["camera"] == "close" and prev.get(lane) == "close":
            f["camera"] = "side"
            f["camera_feel"] = feel_for("side", f.get("interaction", ""), "")
        prev[lane] = f["camera"]
    return frames


def camera_errors(frames: list[dict[str, Any]]) -> list[str]:
    errs, prev = [], {}
    for f in frames:
        lane = f.get("door")
        if f["camera"] == "close" and prev.get(lane) == "close":
            errs.append(f"double_close:{f['slot_id']}")
        prev[lane] = f["camera"]
    return errs


def _auto_rewrite(f: dict[str, Any], errs: list[str]) -> dict[str, Any]:
    s = f.setdefault("scene", {})
    defaults = {"goal": "ГГ чего-то хочет прямо сейчас", "obstacle": "что-то мешает",
                "action": "ГГ делает видимый шаг", "reaction": "мир отвечает",
                "change": "было → стало", "hook": "что дальше?"}
    for k in SCENE_KEYS:
        if not str(s.get(k, "")).strip():
            s[k] = defaults[k]
    if "no_change" in errs:
        f["end_frame"] = f"{f.get('start_frame') or 'кадр'} — после: {s['action']}"
    return f


Rewriter = Callable[[dict[str, Any], list[str]], dict[str, Any]]


def generate(card: Any, rewrite: Rewriter | None = None) -> dict[str, Any]:
    slots = parse_slots(card)
    frames = [build_scene(s) for s in slots]
    log: list[dict[str, Any]] = []
    for i, f in enumerate(frames):
        for attempt in range(MAX_REWRITE):
            errs = validate_scene(f) + validate_frame(f)
            if not errs:
                break
            log.append({"slot_id": f["slot_id"], "attempt": attempt + 1, "errors": errs})
            f = (rewrite or _auto_rewrite)(f, errs)
        frames[i] = f
    frames = fix_camera_sequence(frames)
    fmt = load_template("slots")["format"]
    # бюджет: интро+финал по 5 с, ветви делят остаток поровну (параллельные)
    lanes: dict[str | None, list[dict[str, Any]]] = {}
    for f in frames:
        lanes.setdefault(f["door"], []).append(f)
    for f in lanes.get(None, []):
        f["duration_hint"] = 5.0
    body = fmt["duration_s"] - 5.0 * len(lanes.get(None, []))
    for lane, fs in lanes.items():
        if lane is not None:
            for f in fs:
                f["duration_hint"] = round(body / len(fs), 1)
    return {"template_version": TEMPLATE_VERSION, "format": fmt, "frames": frames,
            "rewrite_log": log, "errors": camera_errors(frames) + [e for f in frames for e in validate_frame(f) + validate_scene(f)]}


def render_plan(result: dict[str, Any]) -> str:
    lines = [f"NII 67 · {result['format']['duration_s']}s {result['format']['aspect']} · шаблоны {result['template_version']}"]
    for n, f in enumerate(result["frames"], 1):
        refs = ", ".join(f"{r['type']}:{r['name']}{'✓' if r['attached'] else '?'}" for r in f["refs"])
        lines.append(f"{n}. [{f['slot_id']}] {f['camera']}/{f['camera_feel']} {f['duration_hint']}s\n"
                     f"   START: {f['start_frame']}\n   END: {f['end_frame']}\n   ДЕЙСТВИЕ: {f['action']}\n   REFS: {refs}")
    return "\n".join(lines)


__all__ = ["parse_slots", "build_scene", "generate", "render_plan", "validate_frame",
           "validate_scene", "camera_errors", "fix_camera_sequence", "KIND_LABEL"]
