"""Закрытый датасет: 10 тем × 5 формулировок = 50 запросов.

CI считает на нём precision@8 hashing-фейком. Порог релиза precision@8 снят:
приёмка живых перефраз — hit@5 и nDCG@8 в контуре B.

У каждой темы 8 релевантных документов с редким якорем. Якорь — часть
разметки, а не секрет: он записан и в документе, и в запросе. Отвлекающие
документы делят бытовые слова пайплайна («кадр», «сцена», «промпт»), но не якорь.
"""

from __future__ import annotations

from typing import Any

THEMES: list[dict[str, Any]] = [
    {
        "id": "stagger",
        "marker": "staggersec4rule",
        "title": "Разнос параллельных батчей",
        "blurb": "Параллельные LLM-батчи разносятся на 2–5 секунд, stagger_sec четыре секунды.",
    },
    {
        "id": "reasoning",
        "marker": "reasoningefforthigh",
        "title": "reasoning effort high",
        "blurb": "Для нод пайплайна и strong-tier нужен высокий reasoning effort.",
    },
    {
        "id": "nodelete",
        "marker": "nodeletionscenes",
        "title": "Запрет удаления сцен",
        "blurb": "Проверки не удаляют сцены и кадры, уменьшение id блокируется.",
    },
    {
        "id": "model",
        "marker": "pay52gpt56sol",
        "title": "Модель 52pay gpt-5.6-sol",
        "blurb": "Ноды пайплайна ходят в модель gpt-5.6-sol через 52pay, запасной канал Kie.",
    },
    {
        "id": "merge",
        "marker": "rmergetrivial",
        "title": "Склейка тривиальных кадров",
        "blurb": "В кадрах соседние постановки склеиваются, если их можно объединить.",
    },
    {
        "id": "wide",
        "marker": "rwidebeforedialog",
        "title": "Общий план перед диалогом",
        "blurb": "Перед репликой стоит общий план, широкий кадр раньше диалога.",
    },
    {
        "id": "vo",
        "marker": "vozover200300",
        "title": "Закадр 200–300 символов",
        "blurb": "Закадр сцены 200–300 символов и раскладывается по кадрам без потерь.",
    },
    {
        "id": "shots",
        "marker": "excelgptfwshots",
        "title": "Нода кадры",
        "blurb": "Нода n_excel_gpt_fw_shots режет сцены в кадры, промпт scenes_to_frames.",
    },
    {
        "id": "qc",
        "marker": "promptsqccontinuity",
        "title": "QC промптов",
        "blurb": "Нода QC проверяет промпты непрерывности и чинит только нарушения.",
    },
    {
        "id": "p65",
        "marker": "tkachfinalochka65",
        "title": "Проект 65 tkach-finalochka",
        "blurb": "Проект 65 tkach-finalochka: кадры для сцен 3–5 и проверка заказа.",
    },
]


def _theme_queries(theme: dict[str, Any]) -> list[str]:
    """Пять формулировок из якоря и общего текста темы.

    Все восемь релевантных документов содержат и якорь, и blurb.
    Запрос не подмешивает слова чужих тем, иначе OR-BM25 поднимает их выше.
    """
    marker = theme["marker"]
    blurb = theme["blurb"]
    title = theme["title"]
    return [
        f"{marker} {blurb}",
        f"{title}. {marker} {blurb}",
        f"Якорь {marker}. {blurb}",
        f"{blurb} {marker} {title}",
        f"{marker} {title}",
    ]


_CORPORA = ("history", "history", "history", "nodes", "nodes", "prompts", "prompts", "prompts")


def build_precision_dataset() -> dict[str, Any]:
    documents: list[dict[str, Any]] = []
    queries: list[dict[str, Any]] = []
    for theme in THEMES:
        source_ids: list[str] = []
        for index, corpus in enumerate(_CORPORA, start=1):
            source_id = f"{theme['id']}-{corpus}-{index:02d}"
            source_ids.append(source_id)
            documents.append(
                {
                    "corpus": corpus,
                    "source_id": source_id,
                    "title": f"{theme['title']} #{index}",
                    "text": (
                        f"{theme['title']}. {theme['blurb']} "
                        f"Якорь {theme['marker']}. Пример {index}, корпус {corpus}."
                    ),
                    "metadata": {
                        "node_key": "n_excel_gpt_fw_shots" if theme["id"] == "shots" else None,
                        "version": 1 if corpus == "prompts" else None,
                        "project_id": 65 if theme["id"] == "p65" else None,
                        "updated_at": "2026-10-17T00:00:00Z",
                    },
                }
            )
        for query_index, text in enumerate(_theme_queries(theme), start=1):
            queries.append(
                {
                    "id": f"{theme['id']}-{query_index:02d}",
                    "query": text,
                    "corpora": ["rules", "history", "nodes", "prompts"],
                    "relevant_source_ids": list(source_ids),
                }
            )
    for index in range(1, 17):
        documents.append(
            {
                "corpus": "history",
                "source_id": f"distractor-{index:02d}",
                "title": f"Отвлекающая заметка {index}",
                "text": (
                    f"Кадр сцены показывает героя. Промпт картинки собран. "
                    f"Закадр короткий. Заметка {index} без редкого якоря."
                ),
                "metadata": {
                    "node_key": None,
                    "version": None,
                    "project_id": None,
                    "updated_at": "2026-10-17T00:00:00Z",
                },
            }
        )
    if len(queries) != 50:
        raise RuntimeError(f"expected 50 queries, got {len(queries)}")
    return {
        "version": 1,
        "description": (
            "50 запросов, у каждого 8 релевантных чанков. "
            "На закрытом наборе CI считает macro precision@8. "
            "Порог релиза precision@8 снят: приёмка живых перефраз — hit@5 и nDCG@8 "
            "в контуре B. Якоря размечены явно, эмбеддер в CI — детерминированный фейк."
        ),
        "documents": documents,
        "queries": queries,
    }
