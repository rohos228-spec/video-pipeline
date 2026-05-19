"""20 visual criteria across 6 groups with weighted-score formula.

Hard-coded per the project ТЗ — do NOT shorten, merge or reorder. The IDs
are stable, used as JSON keys everywhere (project.json, iter.json,
knowledge_base.json, scores.xlsx column headers).

Weighted score:

    weighted_score = sum(group_avg * group_weight) / sum(group_weights)

with group_weights = {A:1.3, B:1.2, C:1.2, D:1.3, E:1.4, F:1.1}.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final


@dataclass(frozen=True)
class Criterion:
    """One of the 20 visual criteria."""

    id: str
    name_ru: str
    group: str
    description_ru: str


@dataclass(frozen=True)
class Group:
    """One of the 6 criterion groups with weight."""

    id: str
    name_ru: str
    weight: float


GROUPS: Final[tuple[Group, ...]] = (
    Group("A", "Цвет и свет", 1.3),
    Group("B", "Детализация по планам", 1.2),
    Group("C", "Текстуры и поверхности", 1.2),
    Group("D", "Персонаж", 1.3),
    Group("E", "Pixel art качество", 1.4),
    Group("F", "Стиль", 1.1),
)

GROUP_WEIGHTS: Final[Mapping[str, float]] = {g.id: g.weight for g in GROUPS}

CRITERIA: Final[tuple[Criterion, ...]] = (
    # Group A — Цвет и свет (1.3)
    Criterion(
        "color_harmony", "Гармония цветов", "A",
        "Сочетание цветов в кадре, нет ли грязи / пестроты / перенасыщения",
    ),
    Criterion(
        "color_palette", "Чистота палитры", "A",
        "Ограниченность палитры, доминантный тон, нет ли случайных цветов",
    ),
    Criterion(
        "light_quality", "Общая глубина и качество света", "A",
        "Естественность освещения, объём через свет, атмосфера",
    ),
    Criterion(
        "light_objects", "Свет на предметах", "A",
        "Как свет ложится на объекты: блики, рефлексы, тени от предметов",
    ),
    Criterion(
        "light_character", "Свет на персонаже", "A",
        "Освещение персонажа: объём, тени на шерсти и одежде, rim light",
    ),
    # Group B — Детализация по планам (1.2)
    Criterion(
        "detail_foreground", "Детализация переднего плана", "B",
        "Проработка объектов на переднем плане, чёткость, текстуры",
    ),
    Criterion(
        "detail_midground", "Детализация среднего плана", "B",
        "Проработка среднего плана, достаточно ли деталей, не пустой ли",
    ),
    Criterion(
        "detail_background", "Детализация дальнего плана", "B",
        "Проработка фона: глубина, воздушная перспектива, не плоский ли",
    ),
    Criterion(
        "spatial_depth", "Общая глубина пространства", "B",
        "Ощущение объёма картинки, разделение планов, не плоская ли",
    ),
    # Group C — Текстуры и поверхности (1.2)
    Criterion(
        "texture_objects", "Текстуры предметов", "C",
        "Качество текстур: дерево как дерево, камень как камень, ткань как ткань",
    ),
    Criterion(
        "texture_surfaces", "Текстуры поверхностей", "C",
        "Пол, стены, земля — есть ли текстура или плоская заливка",
    ),
    # Group D — Персонаж (1.3)
    Criterion(
        "fur_quality", "Качество шерсти котов", "D",
        "Читается ли шерсть, есть ли текстура, не плоская ли заливка",
    ),
    Criterion(
        "fur_detail", "Детализация шерсти", "D",
        "Пряди, оттенки, переходы цвета шерсти, объём",
    ),
    Criterion(
        "clothing_detail", "Детализация одежды", "D",
        "Складки, текстура ткани, физика одежды",
    ),
    Criterion(
        "clothing_physics", "Физика одежды", "D",
        "Одежда ведёт себя естественно: складки в правильных местах, гравитация",
    ),
    # Group E — Pixel art качество (1.4)
    Criterion(
        "pixel_sharpness", "Резкость пикселей", "E",
        "Пиксели чёткие, не размытые, не сглаженные случайно",
    ),
    Criterion(
        "pixel_size", "Толщина пикселей", "E",
        "Консистентный размер пикселя, нет смешения разрешений",
    ),
    Criterion(
        "outline_thickness", "Толщина обводки", "E",
        "Контурные линии одинаковой толщины, нет рваных линий",
    ),
    # Group F — Стиль (1.1)
    Criterion(
        "style_consistency", "Единство стиля", "F",
        "Нет смешения стилей (pixel art + реализм), всё в одном ключе",
    ),
    Criterion(
        "style_artifacts", "Отсутствие артефактов", "F",
        "Нет визуального мусора, глитчей, случайных элементов, искажений",
    ),
)


CRITERION_IDS: Final[tuple[str, ...]] = tuple(c.id for c in CRITERIA)

# Lookup: criterion_id -> Criterion
CRITERION_BY_ID: Final[Mapping[str, Criterion]] = {c.id: c for c in CRITERIA}

# Lookup: group_id -> tuple of criterion_ids in that group
GROUP_TO_CRITERIA: Final[Mapping[str, tuple[str, ...]]] = {
    g.id: tuple(c.id for c in CRITERIA if c.group == g.id) for g in GROUPS
}


def weighted_score(scores: Mapping[str, float]) -> float:
    """Compute the weighted score from per-criterion scores (1..10).

    Missing criteria are skipped (group average uses only present ones).
    Empty groups contribute weight 0. If no criterion is present at all,
    returns 0.0.
    """
    total_weighted = 0.0
    total_weight = 0.0
    for group in GROUPS:
        members = GROUP_TO_CRITERIA[group.id]
        present = [float(scores[m]) for m in members if m in scores]
        if not present:
            continue
        avg = sum(present) / len(present)
        total_weighted += avg * group.weight
        total_weight += group.weight
    if total_weight == 0:
        return 0.0
    return total_weighted / total_weight


def criterion_group(criterion_id: str) -> str:
    """Return group id ('A'..'F') for a criterion id."""
    return CRITERION_BY_ID[criterion_id].group
