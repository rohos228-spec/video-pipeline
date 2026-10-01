"""Карточка сценария NII 67: две параллельные двери и общий финал.

Источник правды на проекте — ``project.meta["nii67_card"]``.
Кнопка «Сгенерировать сценарий» пишет собранный текст в ``general_plan``.
Модель не вызывает GPT: оператор заполняет поля, код только собирает текст.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

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
    "exit": "Выход",
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


def _clip(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


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
            if len(blocks) >= MAX_BLOCKS:
                break
            if not isinstance(item, dict):
                continue
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


def default_card() -> dict[str, Any]:
    return {
        "version": 1,
        "intro": {"text": ""},
        "gg": {"name": "", "note": ""},
        "doors": [_default_door("door1"), _default_door("door2")],
        "finale": {"standard": "", "notes": ""},
        "generated_text": "",
        "saved_at": "",
        "last_save": "",
    }


def normalize_card(raw: Any) -> dict[str, Any]:
    """Починить карточку: ровно Door 1 и Door 2, известные типы блоков, лимиты."""
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
        "intro": {"text": _clip(intro.get("text"), TEXT_LIMIT)},
        "gg": {
            "name": _clip(gg.get("name"), SHORT_LIMIT),
            "note": _clip(gg.get("note"), TEXT_LIMIT),
        },
        "doors": doors,
        "finale": {
            "standard": _clip(finale.get("standard"), FINALE_LIMIT),
            "notes": _clip(finale.get("notes"), FINALE_LIMIT),
        },
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
    if not any(key in raw for key in ("doors", "intro", "gg", "finale")):
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
    door["blocks"] = [block for block in door["blocks"] if block["id"] != block_id]
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
        if str(block.get("exit_action") or "").strip():
            lines.append("Выход: " + str(block["exit_action"]).strip())
        if str(block.get("exit_animation") or "").strip():
            lines.append("Анимация выхода: " + str(block["exit_animation"]).strip())
        if str(block.get("room_after") or "").strip():
            lines.append("Комната после перехода: " + str(block["room_after"]).strip())
        if text:
            lines.append(text)
    elif text:
        lines.append(text)
    return "\n".join(lines)


def compile_scenario(card: dict[str, Any]) -> str:
    """Собрать текст сценария из карточки. Один ГГ на весь ролик, две двери, общий финал."""
    data = normalize_card(card)
    gg_name = str(data["gg"]["name"] or "").strip()
    lines: list[str] = ["ИНТРО", str(data["intro"]["text"] or "").strip() or "—", ""]
    lines.append("ГЛАВНЫЙ ГЕРОЙ (один на всё видео)")
    lines.append("Имя: " + (gg_name or "без имени"))
    note = str(data["gg"]["note"] or "").strip()
    if note:
        lines.append(note)
    lines.append("")
    lines.append("ТЕМАТИЧЕСКИЕ ДВЕРИ")
    lines.append("Сценарий делится на две параллельные ветви, затем сходится в общий финал.")
    for door in data["doors"]:
        lines.append("")
        theme = str(door["theme"] or "").strip() or "тема не задана"
        lines.append(f"{door['title']} — {door['branch']}")
        lines.append(f"Тема: {theme}")
        if str(door["theme_note"] or "").strip():
            lines.append(str(door["theme_note"]).strip())
        if str(door["image_prompt"] or "").strip():
            lines.append("Образ двери: " + str(door["image_prompt"]).strip())
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
