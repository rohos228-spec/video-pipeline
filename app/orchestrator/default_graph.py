"""Дефолтный граф workflow (без зависимости от web)."""

from __future__ import annotations

LAYOUT_VERSION = 10

# Веер scene_design: split → 5 категорийных агентов (параллельно) → сборщик.
# Ноды — обычные «Работа с GPT» (excel_gpt) с маркером data.sd_agent:
# оркестратор гоняет их шагами scene_d/scene_asm, промты — варианты
# sd_<агент> в prompts/05_excel_gpt.
SD_FANOUT: list[tuple[str, str, str]] = [
    ("characters", "GPT: персонажи", "Реестр персонажей и вариации"),
    ("world", "GPT: мир", "Локации, фоны, раскладка предметов"),
    ("style", "GPT: стиль", "Стиль, свет, палитра по этапам"),
    ("camera", "GPT: камера", "Планы/ракурсы/движение (каталог наборов)"),
    ("action", "GPT: действие", "Драматургия, границы сцен, цепи действий"),
]


def default_graph() -> tuple[list[dict], list[dict]]:
    """Возвращает (nodes, edges) дефолтного пайплайна с веером scene_design."""
    steps = [
        ("topic", "Тема", "Тема ролика"),
        ("plan", "Сценарий", "Сценарий ролика"),
        ("script", "Закадровый текст", "Закадровый текст по кадрам"),
        ("split", "Разбивка", "Разбивка на кадры"),
        # ← здесь веер «Работа с GPT» ×5 (sd_agent) + сборщик (ниже)
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
    FAN_Y0 = -90.0  # верхний агент веера
    FAN_DY = 145.0

    nodes: list[dict] = []
    edges: list[dict] = []
    excel_idx = 0
    step_node_ids: list[str] = []
    # idx 0..3 = topic..split; веер занимает 2 колонки (агенты + сборщик);
    # дальше линейная цепочка со сдвигом +2.
    FANOUT_AFTER_IDX = 3  # после split
    for idx, (typ, label, descr) in enumerate(steps):
        x = (
            BASE_X + idx * STEP_X
            if idx <= FANOUT_AFTER_IDX
            else BASE_X + (idx + 2) * STEP_X
        )
        y = MAIN_Y
        node_id = f"n_{typ}"
        if typ == "excel_gpt":
            excel_idx += 1
            node_id = f"n_excel_gpt_{excel_idx}"
        step_node_ids.append(node_id)
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
    # Веер: 5 агентов вертикально + сборщик на главной оси. Тип excel_gpt
    # (настоящие ноды «Работа с GPT»), идентичность агента — data.sd_agent.
    fan_x = float(BASE_X + (FANOUT_AFTER_IDX + 1) * STEP_X)
    for i, (agent, label, descr) in enumerate(SD_FANOUT):
        nodes.append(
            {
                "id": f"n_excel_gpt_sd_{agent}",
                "type": "excel_gpt",
                "position": {"x": fan_x, "y": FAN_Y0 + i * FAN_DY},
                "data": {"label": label, "description": descr, "sd_agent": agent},
            }
        )
    nodes.append(
        {
            "id": "n_excel_gpt_sd_asm",
            "type": "excel_gpt",
            "position": {
                "x": float(BASE_X + (FANOUT_AFTER_IDX + 2) * STEP_X),
                "y": float(MAIN_Y),
            },
            "data": {
                "label": "GPT: сборка сцен",
                "description": "Финальный агент-сборщик: ячейки → scene_registry + attrs кадров",
                "sd_agent": "assemble",
            },
        }
    )

    def _edge(src: str, tgt: str, eid: str) -> dict:
        return {
            "id": eid,
            "source": src,
            "target": tgt,
            "sourceHandle": "out",
            "targetHandle": "in",
        }

    # Линейная часть до split.
    for i in range(FANOUT_AFTER_IDX):
        _e = _edge(nodes[i]["id"], nodes[i + 1]["id"], f"e_{i}")
        edges.append(_e)
    # split → агенты → сборщик → excel_gpt_1.
    for agent, _label, _descr in SD_FANOUT:
        edges.append(
            _edge("n_split", f"n_excel_gpt_sd_{agent}", f"e_split_{agent}")
        )
        edges.append(
            _edge(f"n_excel_gpt_sd_{agent}", "n_excel_gpt_sd_asm", f"e_{agent}_asm")
        )
    edges.append(_edge("n_excel_gpt_sd_asm", "n_excel_gpt_1", "e_asm_excel1"))
    # Остаток линейной цепочки (excel_gpt_1 → … → publish).
    tail = step_node_ids[step_node_ids.index("n_excel_gpt_1") :]
    for i in range(len(tail) - 1):
        edges.append(_edge(tail[i], tail[i + 1], f"e_tail_{i}"))
    return nodes, edges


# Back-compat alias for web layer.
_default_graph = default_graph
