"""Дефолтный шаблон Workflow, который соответствует текущему ProjectStatus-флоу
(plan → script → split → hero/items → enrich×3 → image_prompts → ...).

Сидится один раз на старте сервера. Юзер может его клонировать и редактировать.
"""

from __future__ import annotations

from sqlalchemy import select

from app.db import session_scope
from app.models import Workflow


def _default_graph() -> tuple[list[dict], list[dict]]:
    """Возвращает (nodes, edges) дефолтного линейного пайплайна.

    Координаты в @xyflow/react: (x растёт вправо, y растёт вниз).
    Шаги расположены в две колонки, чтобы влезть в canvas.
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
        ("publish", "12. Публикация", "TikTok / YT Shorts / IG / VK / Likee"),
    ]
    nodes: list[dict] = []
    edges: list[dict] = []
    for idx, (typ, label, descr) in enumerate(steps):
        col = idx % 2
        row = idx // 2
        x = 80 + col * 320
        y = 60 + row * 140
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
    """Если в БД нет ни одного `is_default=True` Workflow — создаём."""
    async with session_scope() as session:
        existing = (
            await session.execute(
                select(Workflow).where(Workflow.is_default == True)  # noqa: E712
            )
        ).scalar_one_or_none()
        if existing is not None:
            return
        nodes, edges = _default_graph()
        wf = Workflow(
            name="Стандартный shorts-пайплайн",
            description="Полный 60–75 сек ролик: план → сценарий → разбивка → "
                        "герои/предметы → enrich → image_prompts → images → "
                        "анимация → видео → аудио → сборка → публикация.",
            nodes=nodes,
            edges=edges,
            is_default=True,
            version=1,
        )
        session.add(wf)
