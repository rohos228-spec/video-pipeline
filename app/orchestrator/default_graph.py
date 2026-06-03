"""Дефолтный граф workflow (без зависимости от web)."""

from __future__ import annotations

LAYOUT_VERSION = 5


def default_graph() -> tuple[list[dict], list[dict]]:
    """Возвращает (nodes, edges) дефолтного линейного пайплайна."""
    steps = [
        ("topic", "0. Тема", "Тема ролика"),
        ("plan", "1. Сценарий", "Сценарий ролика"),
        ("script", "2. Закадровый текст", "Закадровый текст по кадрам"),
        ("split", "3. Разбивка", "Разбивка на кадры"),
        ("enrich_1", "4. Доп. Excel", "xlsx round-trip перед персонажами"),
        ("hero", "5a. Персонажи", "Генерация референсов героев"),
        ("items", "5b. Предметы", "Генерация референсов предметов"),
        ("enrich_2", "6. Доп. Excel", "xlsx round-trip после предметов"),
        ("image_prompts", "7. Промты картинок", "Генерация image-prompt'ов"),
        ("images", "8. Картинки", "Генерация изображений"),
        ("animation_prompts", "9. Промты анимации", "Генерация animation-prompt'ов"),
        ("videos", "10. Видео", "Генерация 8-сек клипов"),
        ("audio", "Озвучка", "ElevenLabs TTS + Whisper"),
        ("music", "11. Музыка", "GPT + Suno (Outsee)"),
        ("assemble", "12. Сборка", "FFmpeg финальный mp4"),
        ("publish", "13. Публикация", "Публикация на 5 площадок"),
    ]
    STEP_X = 290
    BASE_X = 80
    MAIN_Y = 200

    nodes: list[dict] = []
    edges: list[dict] = []
    for idx, (typ, label, descr) in enumerate(steps):
        x = BASE_X + idx * STEP_X
        y = MAIN_Y
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
