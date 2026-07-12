"""Дефолтный граф workflow (без зависимости от web)."""

from __future__ import annotations

LAYOUT_VERSION = 7


def default_graph() -> tuple[list[dict], list[dict]]:
    """Возвращает (nodes, edges) дефолтного линейного пайплайна."""
    steps = [
        ("topic", "Тема", "Тема ролика"),
        ("plan", "Сценарий", "Сценарий ролика"),
        ("script", "Закадровый текст", "Закадровый текст по кадрам"),
        ("split", "Разбивка", "Разбивка на кадры"),
        ("excel_gpt", "Работа с GPT", "xlsx round-trip перед персонажами"),
        ("hero", "Персонажи", "Генерация референсов героев"),
        ("items", "Предметы", "Генерация референсов предметов"),
        ("excel_gpt", "Работа с GPT", "xlsx round-trip после предметов"),
        ("image_prompts", "Промты картинок", "Генерация image-prompt'ов"),
        ("images", "Картинки", "Генерация изображений"),
        ("animation_prompts", "Промты анимации", "Генерация animation-prompt'ов"),
        ("videos", "Видео", "Генерация 8-сек клипов"),
        ("audio", "Озвучка", "ElevenLabs TTS + Whisper"),
        ("music", "Музыка", "GPT + Suno (Outsee)"),
        ("assemble", "Сборка", "FFmpeg финальный mp4"),
        ("publish", "Публикация", "Публикация на 5 площадок"),
    ]
    STEP_X = 290
    BASE_X = 80
    MAIN_Y = 200

    nodes: list[dict] = []
    edges: list[dict] = []
    excel_idx = 0
    for idx, (typ, label, descr) in enumerate(steps):
        x = BASE_X + idx * STEP_X
        y = MAIN_Y
        node_id = f"n_{typ}"
        if typ == "excel_gpt":
            excel_idx += 1
            node_id = f"n_excel_gpt_{excel_idx}"
        nodes.append(
            {
                "id": node_id,
                "type": typ,
                "position": {"x": float(x), "y": float(y)},
                "data": {
                    "label": label,
                    "description": descr,
                    **({"slotIndex": excel_idx} if typ == "excel_gpt" else {}),
                },
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
