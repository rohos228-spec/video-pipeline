"""Карточка сценария NII 67: две параллельные двери и общий финал.

Источник правды на проекте — ``project.meta["nii67_card"]``.
Кнопка «Сгенерировать сценарий» вызывает текстовую модель и заполняет миры,
тексты дверей и финал. Оператор загружает исходное видео и может править поля.
"""

from __future__ import annotations

import copy
import uuid
from collections import defaultdict
from contextvars import ContextVar
from typing import Any

from loguru import logger

_TEXT_ARCHIVE: ContextVar[list[dict[str, str]] | None] = ContextVar(
    "nii67_text_archive", default=None
)

META_KEY = "nii67_card"

TEXT_LIMIT = 4000
FINALE_LIMIT = 500
SHORT_LIMIT = 180
THEME_LIMIT = 240
GENERATED_LIMIT = 20_000
MAX_BLOCKS = 40

BLOCK_KINDS: tuple[str, ...] = (
    "world_frame",
    "enter_action",
    "world_character",
    "character_action",
    "gg_action",
    "exit",
    "custom",
)
CANONICAL_KINDS: tuple[str, ...] = BLOCK_KINDS[:-1]

KIND_LABEL: dict[str, str] = {
    "world_frame": "Первый кадр мира",
    "enter_action": "Действие попадания",
    "world_character": "Появление персонажа мира",
    "character_action": "Действие персонажа",
    "gg_action": "Действие ГГ",
    "exit": "Иллюстрация: что произошло",
    "custom": "Свой блок",
}
KIND_MARK: dict[str, str] = {
    "world_frame": "A",
    "enter_action": "B",
    "world_character": "C",
    "character_action": "D",
    "gg_action": "E",
    "exit": "F",
    "custom": "•",
}

CREATURE_TYPES: tuple[tuple[str, str], ...] = (
    ("human", "Человек"),
    ("beast", "Зверь"),
    ("spirit", "Дух"),
    ("machine", "Механизм"),
    ("myth", "Мифическое"),
    ("other", "Другое"),
)
TEMPERAMENTS: tuple[tuple[str, str], ...] = (
    ("calm", "Спокойный"),
    ("mysterious", "Загадочный"),
    ("friendly", "Дружелюбный"),
    ("wary", "Осторожный"),
    ("harsh", "Жёсткий"),
)
_CREATURE_IDS = {key for key, _label in CREATURE_TYPES}
_TEMPER_IDS = {key for key, _label in TEMPERAMENTS}
_DOOR_IDS = ("door1", "door2")
_DOOR_TITLES = {"door1": ("Door 1", "Ветвь 1"), "door2": ("Door 2", "Ветвь 2")}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _clip(value: Any, limit: int, *, field: str = "") -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) <= limit:
        return text
    bucket = _TEXT_ARCHIVE.get()
    if bucket is not None:
        bucket.append({"field": field or "text", "text": text})
    logger.warning(
        "WARN normalize_card: поле {} длиннее {} ({} симв) — текст сохранён целиком",
        field or "text",
        limit,
        len(text),
    )
    return text


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "да"}
    return False


def _enum(value: Any, allowed: set[str]) -> str:
    if isinstance(value, str) and value in allowed:
        return value
    return ""


def _label(table: tuple[tuple[str, str], ...], key: str) -> str:
    for item_id, label in table:
        if item_id == key:
            return label
    return ""


def empty_character() -> dict[str, Any]:
    return {
        "enabled": False,
        "name": "",
        "creature_type": "",
        "temperament": "",
        "position": "",
        "behavior": "",
        "appearance": "",
    }


def _normalize_character(raw: Any) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    return {
        "enabled": _as_bool(src.get("enabled")),
        "name": _clip(src.get("name"), SHORT_LIMIT),
        "creature_type": _enum(src.get("creature_type"), _CREATURE_IDS),
        "temperament": _enum(src.get("temperament"), _TEMPER_IDS),
        "position": _clip(src.get("position"), TEXT_LIMIT),
        "behavior": _clip(src.get("behavior"), TEXT_LIMIT),
        "appearance": _clip(src.get("appearance"), TEXT_LIMIT),
    }


def new_block(kind: str) -> dict[str, Any]:
    if kind not in KIND_LABEL:
        kind = "custom"
    return {
        "id": _new_id("b"),
        "kind": kind,
        "collapsed": False,
        "text": "",
        "image_prompt": "",
        "image_name": "",
        "custom_title": "",
        "exit_action": "",
        "exit_animation": "",
        "room_after": "",
        "character": empty_character(),
    }


def _normalize_block(raw: Any) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    kind = src.get("kind")
    if kind not in KIND_LABEL:
        kind = "custom"
    block_id = src.get("id")
    if not isinstance(block_id, str) or not block_id.strip():
        block_id = _new_id("b")
    else:
        block_id = block_id.strip()[:40]
    block = new_block(kind)
    block["id"] = block_id
    block["collapsed"] = _as_bool(src.get("collapsed"))
    block["text"] = _clip(src.get("text"), TEXT_LIMIT)
    block["image_prompt"] = _clip(src.get("image_prompt"), TEXT_LIMIT)
    block["image_name"] = _clip(src.get("image_name"), SHORT_LIMIT)
    block["custom_title"] = _clip(src.get("custom_title"), SHORT_LIMIT)
    block["exit_action"] = _clip(src.get("exit_action"), TEXT_LIMIT)
    block["exit_animation"] = _clip(src.get("exit_animation"), TEXT_LIMIT)
    block["room_after"] = _clip(src.get("room_after"), TEXT_LIMIT)
    block["character"] = _normalize_character(src.get("character"))
    note = _clip(src.get("пометка"), TEXT_LIMIT)
    if note:
        block["пометка"] = note
    if _as_bool(src.get("карантин")):
        block["карантин"] = True
    if src.get("активен") is False:
        block["активен"] = False
    return block


def _default_blocks() -> list[dict[str, Any]]:
    return [new_block(kind) for kind in CANONICAL_KINDS]


def _default_door(door_id: str) -> dict[str, Any]:
    title, branch = _DOOR_TITLES[door_id]
    return {
        "id": door_id,
        "title": title,
        "branch": branch,
        "theme": "",
        "theme_note": "",
        "image_prompt": "",
        "image_name": "",
        "blocks": _default_blocks(),
    }


def _normalize_door(door_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    base = _default_door(door_id)
    blocks_raw = raw.get("blocks")
    if blocks_raw is None:
        blocks = base["blocks"]
    elif isinstance(blocks_raw, list):
        blocks = []
        seen: set[str] = set()
        for item in blocks_raw:
            if not isinstance(item, dict):
                continue
            if len(blocks) >= MAX_BLOCKS:
                logger.warning(
                    "WARN normalize_card: блок сверх {} сохранён, не отброшен",
                    MAX_BLOCKS,
                )
            block = _normalize_block(item)
            if block["id"] in seen:
                block["id"] = _new_id("b")
            seen.add(block["id"])
            blocks.append(block)
    else:
        blocks = base["blocks"]
    title = _clip(raw.get("title"), SHORT_LIMIT) or base["title"]
    branch = _clip(raw.get("branch"), SHORT_LIMIT) or base["branch"]
    return {
        "id": door_id,
        "title": title,
        "branch": branch,
        "theme": _clip(raw.get("theme"), THEME_LIMIT),
        "theme_note": _clip(raw.get("theme_note"), TEXT_LIMIT),
        "image_prompt": _clip(raw.get("image_prompt"), TEXT_LIMIT),
        "image_name": _clip(raw.get("image_name"), SHORT_LIMIT),
        "blocks": blocks,
    }


PORTAL_FIELDS = ("image_name", "video_name", "left_design", "right_design", "left_anim", "right_anim")


def default_portal() -> dict[str, str]:
    return {key: "" for key in PORTAL_FIELDS}


def _normalize_portal(raw: Any) -> dict[str, str]:
    src = raw if isinstance(raw, dict) else {}
    out = default_portal()
    for key in PORTAL_FIELDS:
        limit = SHORT_LIMIT if key.endswith("_name") else TEXT_LIMIT
        out[key] = _clip(src.get(key), limit)
    return out


PLAN_OUTCOMES = ("death", "escape", "transformation")
PLAN_LIMIT = 1200


def default_plan() -> dict[str, Any]:
    door = {"world": "", "happens": "", "outcome": "", "category": ""}
    return {"door1": dict(door), "door2": dict(door), "finale": ""}


def normalize_plan(raw: Any) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    out = default_plan()
    for door_id in ("door1", "door2"):
        item = src.get(door_id) if isinstance(src.get(door_id), dict) else {}
        outcome = str(item.get("outcome") or "").strip()
        out[door_id] = {
            "world": _clip(item.get("world"), PLAN_LIMIT),
            "happens": _clip(item.get("happens"), PLAN_LIMIT),
            "outcome": outcome if outcome in PLAN_OUTCOMES else "",
            "category": _clip(item.get("category"), 60),
        }
    out["finale"] = _clip(src.get("finale"), PLAN_LIMIT)
    return out


def plan_ready(plan: dict[str, Any]) -> bool:
    return all(str(plan[d]["world"]).strip() and str(plan[d]["happens"]).strip() for d in ("door1", "door2"))


def default_card() -> dict[str, Any]:
    return {
        "version": 1,
        "intro": {"text": "", "video_name": ""},
        "portal": default_portal(),
        "gg": {"name": "", "note": ""},
        "doors": [_default_door("door1"), _default_door("door2")],
        "finale": {"standard": "", "notes": ""},
        "plan": default_plan(),
        "generated_text": "",
        "saved_at": "",
        "last_save": "",
    }


def normalize_card(raw: Any) -> dict[str, Any]:
    """Починить карточку: ровно Door 1 и Door 2, известные типы блоков, лимиты."""
    bucket: list[dict[str, str]] = []
    token = _TEXT_ARCHIVE.set(bucket)
    try:
        card = _normalize_card_body(raw)
    finally:
        _TEXT_ARCHIVE.reset(token)
    if bucket:
        card["text_archive"] = bucket
    return card


def _normalize_card_body(raw: Any) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    found: dict[str, dict[str, Any]] = {}
    doors_raw = src.get("doors")
    if isinstance(doors_raw, list):
        for item in doors_raw:
            if not isinstance(item, dict):
                continue
            door_id = item.get("id")
            if door_id not in _DOOR_IDS or door_id in found:
                continue
            found[str(door_id)] = _normalize_door(str(door_id), item)
    doors = [found.get(door_id) or _default_door(door_id) for door_id in _DOOR_IDS]
    intro = src.get("intro") if isinstance(src.get("intro"), dict) else {}
    gg = src.get("gg") if isinstance(src.get("gg"), dict) else {}
    finale = src.get("finale") if isinstance(src.get("finale"), dict) else {}
    last_save = src.get("last_save")
    if last_save not in {"", "draft", "save"}:
        last_save = ""
    return {
        "version": 1,
        "intro": {
            "text": _clip(intro.get("text"), TEXT_LIMIT),
            "video_name": _clip(intro.get("video_name"), SHORT_LIMIT),
        },
        "portal": _normalize_portal(src.get("portal")),
        "gg": {
            "name": _clip(gg.get("name"), SHORT_LIMIT),
            "note": _clip(gg.get("note"), TEXT_LIMIT),
        },
        "doors": doors,
        "finale": {
            "standard": _clip(finale.get("standard"), FINALE_LIMIT),
            "notes": _clip(finale.get("notes"), FINALE_LIMIT),
        },
        "plan": normalize_plan(src.get("plan")),
        "generated_text": _clip(src.get("generated_text"), GENERATED_LIMIT),
        "saved_at": _clip(src.get("saved_at"), 40),
        "last_save": last_save,
    }


def card_from_meta(meta: Any) -> dict[str, Any]:
    raw = meta.get(META_KEY) if isinstance(meta, dict) else None
    if not isinstance(raw, dict):
        return default_card()
    return normalize_card(raw)


def card_from_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("json object required")
    raw = payload.get("card") if isinstance(payload.get("card"), dict) else payload
    if not isinstance(raw, dict):
        raise ValueError("card object required")
    if not any(key in raw for key in ("doors", "intro", "gg", "finale", "plan")):
        raise ValueError("card object required")
    return normalize_card(raw)


def _with_door(card: dict[str, Any], door_id: str) -> dict[str, Any] | None:
    for door in card["doors"]:
        if door["id"] == door_id:
            return door
    return None


def insert_block(card: dict[str, Any], door_id: str, index: int, kind: str) -> dict[str, Any]:
    out = normalize_card(card)
    door = _with_door(out, door_id)
    if door is None or len(door["blocks"]) >= MAX_BLOCKS:
        return out
    if kind not in KIND_LABEL:
        kind = "custom"
    idx = max(0, min(int(index), len(door["blocks"])))
    door["blocks"].insert(idx, new_block(kind))
    return out


def duplicate_block(card: dict[str, Any], door_id: str, block_id: str) -> dict[str, Any]:
    out = normalize_card(card)
    door = _with_door(out, door_id)
    if door is None or len(door["blocks"]) >= MAX_BLOCKS:
        return out
    for index, block in enumerate(door["blocks"]):
        if block["id"] != block_id:
            continue
        copy_block = copy.deepcopy(block)
        copy_block["id"] = _new_id("b")
        copy_block["collapsed"] = False
        door["blocks"].insert(index + 1, copy_block)
        break
    return out


def delete_block(card: dict[str, Any], door_id: str, block_id: str) -> dict[str, Any]:
    out = normalize_card(card)
    door = _with_door(out, door_id)
    if door is None:
        return out
    before_blocks = [dict(block) for block in door["blocks"]]
    door["blocks"] = [block for block in door["blocks"] if block["id"] != block_id]
    from app.pipeline.no_deletions_guard import keep_soft_kadry

    door["blocks"] = keep_soft_kadry(
        before_blocks,
        door["blocks"],
        scene_id=door_id,
        number=1,
    )
    return out


def move_block(card: dict[str, Any], door_id: str, block_id: str, delta: int) -> dict[str, Any]:
    out = normalize_card(card)
    door = _with_door(out, door_id)
    if door is None:
        return out
    index = next((i for i, block in enumerate(door["blocks"]) if block["id"] == block_id), None)
    if index is None:
        return out
    target = index + int(delta)
    if target < 0 or target >= len(door["blocks"]):
        return out
    blocks = door["blocks"]
    blocks[index], blocks[target] = blocks[target], blocks[index]
    return out


def reorder_block(card: dict[str, Any], door_id: str, from_index: int, to_index: int) -> dict[str, Any]:
    out = normalize_card(card)
    door = _with_door(out, door_id)
    if door is None:
        return out
    blocks = door["blocks"]
    if not blocks:
        return out
    src = int(from_index)
    dst = int(to_index)
    if src < 0 or dst < 0 or src >= len(blocks) or dst >= len(blocks) or src == dst:
        return out
    item = blocks.pop(src)
    blocks.insert(dst, item)
    return out


def update_block(
    card: dict[str, Any], door_id: str, block_id: str, patch: dict[str, Any]
) -> dict[str, Any]:
    out = normalize_card(card)
    door = _with_door(out, door_id)
    if door is None or not isinstance(patch, dict):
        return out
    for index, block in enumerate(door["blocks"]):
        if block["id"] != block_id:
            continue
        merged = copy.deepcopy(block)
        for key in (
            "text",
            "image_prompt",
            "image_name",
            "custom_title",
            "exit_action",
            "exit_animation",
            "room_after",
            "collapsed",
        ):
            if key in patch:
                merged[key] = patch[key]
        if isinstance(patch.get("character"), dict):
            character = dict(merged["character"])
            character.update(patch["character"])
            merged["character"] = character
        door["blocks"][index] = _normalize_block(merged)
        break
    return out


def update_intro(card: dict[str, Any], text: str) -> dict[str, Any]:
    out = normalize_card(card)
    out["intro"]["text"] = _clip(text, TEXT_LIMIT)
    return out


def update_gg(card: dict[str, Any], *, name: str | None = None, note: str | None = None) -> dict[str, Any]:
    out = normalize_card(card)
    if name is not None:
        out["gg"]["name"] = _clip(name, SHORT_LIMIT)
    if note is not None:
        out["gg"]["note"] = _clip(note, TEXT_LIMIT)
    return out


def update_door(card: dict[str, Any], door_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    out = normalize_card(card)
    door = _with_door(out, door_id)
    if door is None or not isinstance(patch, dict):
        return out
    for key in ("theme", "theme_note", "image_prompt", "image_name", "title", "branch"):
        if key in patch:
            door[key] = patch[key]
    return _replace_door(out, door_id, _normalize_door(door_id, door))


def _replace_door(card: dict[str, Any], door_id: str, door: dict[str, Any]) -> dict[str, Any]:
    card["doors"] = [door if item["id"] == door_id else item for item in card["doors"]]
    return card


_BEAT_KIND = {
    "entry": "world_frame",
    "law": "enter_action",
    "threat": "world_character",
    "peak": "character_action",
    "outcome": "gg_action",
    "hook": "exit",
}


def _keep_operator_block(
    block: dict[str, Any], reason: str, *, quarantine: bool = True
) -> None:
    if block.get("пометка"):
        return
    block["пометка"] = reason
    if quarantine:
        block["карантин"] = True
    logger.warning(
        "WARN apply_generated_scenario: блок {} сохранён, не заменён: {}",
        block.get("id"),
        reason,
    )


def _merge_generated_beat(
    block: dict[str, Any], beat: dict[str, Any], scenario: dict[str, Any]
) -> dict[str, Any]:
    """Вписать бит в существующий блок. Текст и файлы оператора не затирать."""
    kind = str(block.get("kind") or "")
    text = str(beat.get("text") or "")
    if kind == "exit":
        text = str(scenario.get("illustration") or text)
    if str(block.get("text") or "").strip():
        if text.strip() and text.strip() != str(block.get("text") or "").strip():
            _keep_operator_block(block, "текст оператора сохранён")
    else:
        block["text"] = text
    frames = beat.get("frames") if isinstance(beat.get("frames"), list) else []
    generated_prompt = ""
    if kind == "world_frame" and frames and isinstance(frames[0], dict):
        generated_prompt = str(frames[0].get("start_frame") or "")
    current_prompt = str(block.get("image_prompt") or "").strip()
    if current_prompt:
        if generated_prompt and generated_prompt.strip() != current_prompt:
            _keep_operator_block(block, "image_prompt оператора сохранён")
    elif generated_prompt:
        block["image_prompt"] = generated_prompt
    if str(block.get("image_name") or "").strip():
        _keep_operator_block(block, "image_name оператора сохранён")
    return block


def apply_generated_scenario(
    card: dict[str, Any],
    doors: dict[str, dict[str, Any]],
    finale: dict[str, str],
) -> dict[str, Any]:
    """Вписать сгенерированные миры в существующие поля. Загрузки файлов не трогать."""
    out = normalize_card(card)
    if not str(out["gg"]["name"] or "").strip():
        out["gg"]["name"] = "Герой"
    for door_id, scenario in doors.items():
        door = _with_door(out, door_id)
        if door is None or not isinstance(scenario, dict):
            continue
        door["theme"] = scenario.get("theme") or ""
        door["theme_note"] = scenario.get("law") or ""
        pending: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for beat in scenario.get("beats") or []:
            if not isinstance(beat, dict):
                continue
            kind = _BEAT_KIND.get(str(beat.get("id") or ""))
            if kind is None:
                continue
            pending[kind].append(beat)
        before_blocks = [dict(block) for block in door["blocks"]]
        merged_blocks: list[dict[str, Any]] = []
        for block in list(door["blocks"]):
            queue = pending.get(str(block.get("kind") or ""))
            if queue:
                merged_blocks.append(_merge_generated_beat(block, queue.pop(0), scenario))
            else:
                _keep_operator_block(
                    block,
                    "блок оператора сохранён: генерация его не заменила",
                    quarantine=False,
                )
                merged_blocks.append(block)
        for kind, beats_left in pending.items():
            for beat in beats_left:
                merged_blocks.append(_merge_generated_beat(new_block(kind), beat, scenario))
        from app.pipeline.no_deletions_guard import keep_soft_kadry

        door["blocks"] = keep_soft_kadry(
            before_blocks,
            merged_blocks,
            scene_id=door_id,
            number=1,
        )
        side = "left" if door_id == "door1" else "right"
        out["portal"][f"{side}_design"] = str(scenario.get("door_design") or "")
        out["portal"][f"{side}_anim"] = str(scenario.get("door_anim") or "")
    if "standard" in finale:
        out["finale"]["standard"] = finale.get("standard") or ""
    if "notes" in finale:
        out["finale"]["notes"] = finale.get("notes") or ""
    return normalize_card(out)


def update_finale(
    card: dict[str, Any], *, standard: str | None = None, notes: str | None = None
) -> dict[str, Any]:
    out = normalize_card(card)
    if standard is not None:
        out["finale"]["standard"] = _clip(standard, FINALE_LIMIT)
    if notes is not None:
        out["finale"]["notes"] = _clip(notes, FINALE_LIMIT)
    return out


def block_heading(block: dict[str, Any]) -> str:
    kind = str(block.get("kind") or "custom")
    mark = KIND_MARK.get(kind, "•")
    if kind == "custom":
        title = str(block.get("custom_title") or "").strip() or KIND_LABEL["custom"]
    else:
        title = KIND_LABEL.get(kind, KIND_LABEL["custom"])
    return f"{mark}. {title}"


def _block_body(block: dict[str, Any], gg_name: str) -> str:
    kind = block["kind"]
    lines: list[str] = []
    text = str(block.get("text") or "").strip()
    if kind == "world_frame":
        if str(block.get("image_prompt") or "").strip():
            lines.append("Кадр: " + str(block["image_prompt"]).strip())
        if str(block.get("image_name") or "").strip():
            lines.append("Файл слота: " + str(block["image_name"]).strip())
        if text:
            lines.append(text)
    elif kind == "world_character":
        character = block.get("character") or {}
        if not character.get("enabled"):
            lines.append("Персонаж мира не используется.")
        else:
            if str(character.get("name") or "").strip():
                lines.append("Имя: " + str(character["name"]).strip())
            creature = _label(CREATURE_TYPES, str(character.get("creature_type") or ""))
            if creature:
                lines.append("Тип: " + creature)
            temper = _label(TEMPERAMENTS, str(character.get("temperament") or ""))
            if temper:
                lines.append("Характер: " + temper)
            if str(character.get("position") or "").strip():
                lines.append("Позиция: " + str(character["position"]).strip())
            if str(character.get("behavior") or "").strip():
                lines.append("Поведение: " + str(character["behavior"]).strip())
            if str(character.get("appearance") or "").strip():
                lines.append("Эффект появления: " + str(character["appearance"]).strip())
        if text:
            lines.append(text)
    elif kind == "gg_action":
        who = gg_name.strip() or "ГГ"
        lines.append(f"Действие единственного главного героя ({who}).")
        if text:
            lines.append(text)
    elif kind == "exit":
        what = text or " ".join(
            str(block.get(k) or "").strip() for k in ("exit_action", "exit_animation", "room_after")
        ).strip()
        lines.append("Что произошло с героем (по описанию генерируется иллюстрация): " + (what or "—"))
    elif text:
        lines.append(text)
    return "\n".join(lines)


def compile_scenario(card: dict[str, Any]) -> str:
    """Собрать текст сценария из карточки. Один ГГ на весь ролик, две двери, общий финал."""
    data = normalize_card(card)
    gg_name = str(data["gg"]["name"] or "").strip()
    video = str(data["intro"].get("video_name") or "").strip()
    lines: list[str] = ["ИНТРО", "Исходное видео: " + (video or "не загружено"), ""]
    lines.append("ГЛАВНЫЙ ГЕРОЙ (один на всё видео)")
    lines.append("Имя: " + (gg_name or "без имени"))
    note = str(data["gg"]["note"] or "").strip()
    if note:
        lines.append(note)
    lines.append("")
    portal = data["portal"]
    lines.append("ДВЕРИ (одно фото, одно видео трансформации)")
    lines.append("Фото дверей: " + (portal["image_name"] or "не загружено"))
    lines.append("Видео трансформации: " + (portal["video_name"] or "не загружено"))
    lines.append("Левая дверь: " + (portal["left_design"] or "—"))
    lines.append("Анимация левой: " + (portal["left_anim"] or "—"))
    lines.append("Правая дверь: " + (portal["right_design"] or "—"))
    lines.append("Анимация правой: " + (portal["right_anim"] or "—"))
    lines.append("")
    lines.append("Сценарий делится на две параллельные ветви, затем сходится в общий финал.")
    for door in data["doors"]:
        lines.append("")
        lines.append(f"{door['title']} — {door['branch']}")
        if str(door.get("theme") or "").strip():
            lines.append("Мир: " + str(door["theme"]).strip())
        if str(door.get("theme_note") or "").strip():
            lines.append("Закон: " + str(door["theme_note"]).strip())
        for index, block in enumerate(door["blocks"], start=1):
            lines.append("")
            lines.append(f"{index}. {block_heading(block)}")
            body = _block_body(block, gg_name)
            if body:
                lines.append(body)
    lines.append("")
    lines.append("ОБЩИЙ ФИНАЛ")
    lines.append("Стандарт:")
    lines.append(str(data["finale"]["standard"] or "").strip() or "—")
    lines.append("Заметки:")
    lines.append(str(data["finale"]["notes"] or "").strip() or "—")
    return "\n".join(lines).strip() + "\n"
