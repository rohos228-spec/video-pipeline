"""Агент разбора стиля референс-роликов → паспорт стиля → раскладка в Studio.

Состав (см. docs/style-agent-design.md):

- ``schema``    — паспорт v1, поля по осям, валидация ответов специалистов.
- ``budget``    — жёсткий лимит LLM-вызовов на запуск + оценка стоимости.
- ``llm``       — единая точка вызова GPT (моки в тестах, учёт бюджета).
- ``extract``   — детерминированное извлечение: ffprobe/ffmpeg, кадры, листы,
                  голос/музыка, ASR (``app.services.asr``), метрики.
- ``agents``    — специалисты (рисовка / озвучка+звук / анимация+монтаж /
                  повествование) + оркестратор прогона одного ролика.
- ``aggregate`` — устойчивый стиль по N роликам (медиана/IQR, голосование,
                  кластеры палитры) + агент-синтезатор текстовых полей.
- ``apply``     — материализация паспорта в prompts/styles, blocks,
                  style_<id>.md, Project.meta.node_step_params.
- ``job``       — фоновый запуск из ноды ``style_extract`` на холсте,
                  состояние в ``project.meta["style_agent"]``.
"""

from __future__ import annotations

STYLE_EXTRACT_NODE_TYPE = "style_extract"
HITL_STYLE_NODE_TYPE = "hitl_style"
META_KEY = "style_agent"
STYLE_DIR_NAME = "style"

__all__ = [
    "HITL_STYLE_NODE_TYPE",
    "META_KEY",
    "STYLE_DIR_NAME",
    "STYLE_EXTRACT_NODE_TYPE",
]
