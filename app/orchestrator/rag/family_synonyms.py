"""Синонимы семейств: расширение запроса, не веса.

Пары — уравнения из документа семейства, не из вопросов эвала.
«1 бит = 1 слот» в скелете, «Бит = одно смысловое изменение» в сценарии,
«ЧАСТИЧНАЯ СБОРКА» у сборщика, заголовок «ВНЕШНОСТЬ … КАСТИНГ-LOOK»
у персонажей, «освещение» и «Свет внутри локации» у мира.

Лемма дописывается, только если она закреплена за одним семейством и
этой леммы нет в документах сильных семейств: action, shots, script,
rules, camera. Общее слово запрос не меняет.
"""

from __future__ import annotations

import re
from functools import lru_cache

from app.orchestrator.rag.lexical import _fold, _lemma

_WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+")

# Семейство → пары поверхностных слов. Второе слово дописывается к запросу.
_PAIRS: dict[str, tuple[tuple[str, str], ...]] = {
    "world": (("освещение", "свет"),),
    "characters": (("кастинг", "внешность"),),
    "assemble": (("частичный", "сборка"),),
    "skeleton": (("слот", "бит"),),
    # Тот же «бит», что у скелета: лемма в двух семействах и не расширяется.
    "script": (("бит", "изменение"),),
}

# Сильные семейства. Слово отсюда не становится триггером расширения.
_STRONG_DOCS: tuple[str, ...] = (
    "prompts/scene_design/camera.md",
    "prompts/scene_design/camera_sets.md",
    "templates/excel_gpt_agents/sd_camera_chrono_dyn.md",
    "prompts/scene_design/action.md",
    "templates/excel_gpt_agents/sd_action_chrono_dyn.md",
    "templates/excel_gpt_agents/main_action_from_bits_ru.md",
    "templates/excel_gpt_agents/scenes_to_frames_ru.md",
    "templates/excel_gpt_agents/frame_prompts_continuity_ru.md",
    "templates/excel_gpt_agents/script_writer_ru.md",
    "docs/fable/user_rules.md",
)


@lru_cache(maxsize=1)
def _strong_lemmas() -> frozenset[str]:
    from app.project_root import find_project_root

    root = find_project_root()
    found: set[str] = set()
    for rel in _STRONG_DOCS:
        text = (root / rel).read_text(encoding="utf-8")
        for raw in _WORD.findall(text):
            found.add(_lemma(_fold(raw)))
    return frozenset(found)


@lru_cache(maxsize=1)
def partner_table() -> dict[str, str]:
    """Лемма запроса → слово, которое можно дописать. Общие леммы сюда не входят."""
    owners: dict[str, set[str]] = {}
    partner_of: dict[str, str] = {}
    for family, pairs in _PAIRS.items():
        for left, right in pairs:
            left_lemma = _lemma(_fold(left))
            right_lemma = _lemma(_fold(right))
            owners.setdefault(left_lemma, set()).add(family)
            owners.setdefault(right_lemma, set()).add(family)
            partner_of.setdefault(left_lemma, right)
            partner_of.setdefault(right_lemma, left)
    blocked = _strong_lemmas()
    table: dict[str, str] = {}
    for lemma, families in owners.items():
        if len(families) != 1 or lemma in blocked:
            continue
        table[lemma] = partner_of[lemma]
    return table


def expand_family_query(text: str) -> str:
    """Дописывает синоним семейства. Текст без таких лемм возвращается как был."""
    table = partner_table()
    words = _WORD.findall(text or "")
    if not words or not table:
        return text
    present = {_lemma(_fold(word)) for word in words}
    extras: list[str] = []
    seen: set[str] = set()
    for word in words:
        partner = table.get(_lemma(_fold(word)))
        if not partner or partner in seen:
            continue
        if _lemma(_fold(partner)) in present:
            continue
        seen.add(partner)
        extras.append(partner)
    if not extras:
        return text
    return f"{text} {' '.join(extras)}"
