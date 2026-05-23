"""Дефолтный шаблон Workflow.

Сидится один раз на старте сервера. Юзер может его клонировать и редактировать.

Если меняешь раскладку нод (`_default_graph`) — обяззательно бампь
`LAYOUT_VERSION`, иначе уже посеянный workflow не обновится.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db import session_scope
from app.models import Workflow

# Bump при изменении раскладки → существующий default-workflow обновится.
LAYOUT_VERSION = 2


def _default_graph() -> tuple[list[dict], list[dict]]:
    """Возвращает (nodes, edges) дефолтного линейного пайплайна.

    Координаты в @xyflow/react: (x растёт вправо, y растёт вниз).
    Раскладка: горизонтальная, слева направо. HITL-гейты слегка
    приподняты (выше по Y), чтобы визуально отличались от обычных нод
    основной цепочки. Canvas прокручивается, fitView подгонит зум.
    """
    steps = [
        ("plan", "1. План", "Общий план ролика"),
        ("script", "2. Сценарий", "Сценарий → закадровые тексты"),
        ("split", "3. Разбивка", "Разбивка на кадры"),
        ("hero", "4a. Персонажи", "Генерация референсов героев"),
        ("hitl_hero", "HITL: персонажи", "Одобрение героев"),
        ("items", "4b. Предметы", "Генерация референсов предметов"),
        ("enrich_1", "5.1. Доп. Excel", "xlsx round-trip"),
        ("enrich_2", "5.2. Доп. Excel", "xlsx round-trip"),
        ("enrich_3", "5.3. Доп. Excel", "xlsx round-trip"),
        ("image_prompts", "6. Промты картинок", "Генерация image-prompt'ов"),
        ("images", "7. Картинки", "Генерация изображений"),
        ("hitl_images", "HITL: картинки", "Одобрение изображений"),
        ("animation_prompts", "8. Промты анимации", "Генерация animation-prompt'ов"),
        ("videos", "9. Видео", "Генерация 8-сек клипов"),
        ("hitl_videos", "HITL: видео", "Одобрение видео"),
        ("audio", "10. Аудио", "ElevenLabs TTS + Whisper"),
        ("assemble", "11. Сборка", "FFmpeg финальный mp4"),
        ("hitl_final", "HITL: финал", "Одобрение финала"),
        ("publish", "12. Публикация", "Публикация на 5 площадок"),
    ]
    # Геометрия: ширина ноды ≈ 244, желаемый шаг = 290, чтобы edges не сливались.
    STEP_X = 290
    BASE_X = 80
    MAIN_Y = 200       # основная горизонтальная линия
    HITL_Y_OFFSET = -120  # HITL чуть выше — образует «волну» подтверждений

    nodes: list[dict] = []
    edges: list[dict] = []
    for idx, (typ, label, descr) in enumerate(steps):
        x = BASE_X + idx * STEP_X
        y = MAIN_Y + (HITL_Y_OFFSET if typ.startswith("hitl_") else 0)
        nodes.append({
            "id": f"n_{typ}",
            "type": typ,
            "position": {"x": float(x), "y": float(y)},
            "data": {"label": label, "description": descr},
        })
    for i in range(len(steps) - 1):
        src = nodes[i]["id"]
        tgt = nodes[i + 1]["id"]
        edges.append({
            "id": f"e_{i}",
            "source": src,
            "target": tgt,
            "sourceHandle": "out",
            "targetHandle": "in",
        })
    return nodes, edges


async def seed_default_workflow() -> None:
    """Создаёт или обновляет системный default Workflow.

    Если уже есть запись с `is_default=True` — обновляем nodes/edges
    при условии, что её `meta.layout_version` устарела (иначе пользовательские
    правки не затрём, но визуально-важные миграции прокатятся).
    """
    nodes, edges = _default_graph()
    async with session_scope() as session:
        existing = (
            await session.execute(
                select(Workflow).where(Workflow.is_default == True)  # noqa: E712
            )
        ).scalar_one_or_none()

        if existing is None:
            wf = Workflow(
                name="Стандартный shorts-пайплайн",
                description=(
                    "Полный 60–75 сек ролик: план → сценарий → разбивка → "
                    "герои/предметы → enrich → image_prompts → images → "
                    "анимация → видео → аудио → сборка → публикация."
                ),
                nodes=nodes,
                edges=edges,
                is_default=True,
                version=1,
                meta={"layout_version": LAYOUT_VERSION},
            )
            session.add(wf)
            return

        meta = dict(existing.meta or {})
        if meta.get("layout_version") != LAYOUT_VERSION:
            existing.nodes = nodes
            existing.edges = edges
            meta["layout_version"] = LAYOUT_VERSION
            existing.meta = meta
            existing.version = (existing.version or 1) + 1
