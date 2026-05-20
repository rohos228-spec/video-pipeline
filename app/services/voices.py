"""Каталог голосов 11labs для шага 10.

Источник — `prompts/voices.json`. Файл правится руками (см. шаблон в репо).
Структура:
    {
        "voices": [
            {"name": "...", "url": "https://elevenlabs.io/.../<voice>"},
            ...
        ]
    }

Имя из `name` попадает в dropdown'а столбца U "Голос" в topics.xlsx (см.
`app/storage/batch_sheet.py`). Шаг 10 берёт name из карточки топика и ищет
соответствующий URL здесь.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

_VOICES_PATH = Path("prompts/voices.json")


@dataclass(frozen=True)
class Voice:
    name: str
    url: str


def _load_raw() -> list[dict]:
    if not _VOICES_PATH.exists():
        logger.warning("voices: {} не найден, список пуст", _VOICES_PATH)
        return []
    try:
        with _VOICES_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:  # noqa: BLE001
        logger.warning("voices: не смог прочитать {}: {}", _VOICES_PATH, e)
        return []
    voices = data.get("voices") if isinstance(data, dict) else None
    if not isinstance(voices, list):
        logger.warning("voices: ожидался ключ 'voices' со списком в {}", _VOICES_PATH)
        return []
    return voices


def list_voices() -> list[Voice]:
    """Прочитать `prompts/voices.json` и вернуть список Voice.

    Голоса без `name`/`url` пропускаются. Дубликаты по `name` оставляются как
    есть — Excel-валидация просто покажет их в dropdown'е.
    """
    out: list[Voice] = []
    for raw in _load_raw():
        if not isinstance(raw, dict):
            continue
        name = (raw.get("name") or "").strip()
        url = (raw.get("url") or "").strip()
        if not name or not url:
            continue
        out.append(Voice(name=name, url=url))
    return out


def voice_names() -> list[str]:
    """Список имён голосов для dropdown'а в xlsx."""
    return [v.name for v in list_voices()]


def find_voice(name: str | None) -> Voice | None:
    """Найти голос по имени (точное совпадение). Возвращает None, если нет."""
    if not name:
        return None
    target = name.strip()
    if not target:
        return None
    for v in list_voices():
        if v.name == target:
            return v
    return None
