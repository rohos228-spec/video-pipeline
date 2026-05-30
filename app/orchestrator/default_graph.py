"""Дефолтный граф workflow (без зависимости от web)."""

from __future__ import annotations

LAYOUT_VERSION = 3


def default_graph() -> tuple[list[dict], list[dict]]:
    """Возвращает (nodes, edges) дефолтного линейного пайплайна."""
    steps = [
        ("topic", "0. Тема", "Тема ролика"),
        ("plan", "1. Сценарий", "Сценарий ролика"),
        ("script", "2. Закадровый текст", "Закадровый текст по кадрам"),
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
    STEP_X = 290
    BASE_X = 80
    MAIN_Y = 200
    HITL_Y_OFFSET = -120

    nodes: list[dict] = []
    edges: list[dict] = []
    for idx, (typ, label, descr) in enumerate(steps):
        x = BASE_X + idx * STEP_X
        y = MAIN_Y + (HITL_Y_OFFSET if typ.startswith("hitl_") else 0)
        nodes.append(
            {
                "id": f"n_{typ}",
                "type": typ,
                "position": {"x": float(x), "y": float(y)},
                "data": {"label": label, "description": descr},
            }
        )
    for i in range(len(steps) - 1):
        src = nodes[i]["id"]
        tgt = nodes[i + 1]["id"]
        edges.append(
            {
                "id": f"e_{i}",
                "source": src,
                "target": tgt,
                "sourceHandle": "out",
                "targetHandle": "in",
            }
        )
    return nodes, edges


# Back-compat alias for web layer.
_default_graph = default_graph
