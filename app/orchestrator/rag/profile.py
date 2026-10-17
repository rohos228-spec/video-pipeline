"""Явный профиль контура B. Сам по себе веса не грузит.

``get_engine()`` без профиля и CI остаются на hashing без реранкера.
Включение:

- ``ORCH_RAG_PROFILE=contour-b`` или ``get_engine(profile="contour-b")``
  строит embedder и реранкер и кладёт k, веса, scope и пул в ``retrieve``.
- ``ORCH_RAG_RERANK=0`` или ``retrieve(..., rerank=False)`` выключает реранкер.
- После смены переменных нужен ``reset_engine()``: синглтон уже собранный движок не перечитывает.

Ручная сборка без синглтона::

    RagEngine(
        embedder=build_embedder(CONTOUR_B_EMBEDDER),
        reranker=(
            build_reranker(CONTOUR_B_RERANKER, max_length=CONTOUR_B_RERANK_MAX_LENGTH)
            if CONTOUR_B_RERANKER != "none"
            else None
        ),
    )
    engine.retrieve(
        query,
        rrf_k=CONTOUR_B_RRF_K,
        rrf_weights=(CONTOUR_B_RRF_LEXICAL, CONTOUR_B_RRF_VECTOR),
        bm25_scope=CONTOUR_B_BM25_SCOPE,
        rerank_pool=CONTOUR_B_RERANK_POOL,
        aggregate=CONTOUR_B_AGGREGATE,
        rerank=False,  # гибрид без реранкера
    )

Дефолт — вариант C (8 частей, метки s0–s7) с bge-m3.
Подбор на нечётных query_id (dev), до него сплит зафиксирован.
Порог nDCG@8 0.65 не снижался: это приёмка R5.

Выбранный конфиг: RRF k=20, веса лексика/вектор 0.5/1.5, BM25 full,
реранк топ-12, агрегация ``prompt_pool`` (части одного промпта внутри пула).
Эмбеддинги чанков и повторных запросов кэшируются. p95 — медиана трёх прогонов,
на test 0.914 с, на всех запросах 0.926 с: порог 1.2 с взят.
Медиана nDCG@8 на test — 0.6372. Порог 0.65 на test не взят и не снижался.
``ORCH_RAG_RERANK=0`` или ``retrieve(..., rerank=False)`` оставляют гибрид.
Цифры — в ``evals/orchestrator/datasets/precision_live_report.md``.
"""

from __future__ import annotations

# Обновляется вместе с отчётом контура B. get_engine() читает их только при profile=contour-b.
CONTOUR_B_EMBEDDER = "e5-large"
CONTOUR_B_RERANKER = "bge-m3"
CONTOUR_B_RRF_K = 20
CONTOUR_B_RRF_LEXICAL = 0.5
CONTOUR_B_RRF_VECTOR = 1.5
CONTOUR_B_BM25_SCOPE = "full"
CONTOUR_B_RERANK_POOL = 12
CONTOUR_B_AGGREGATE = "prompt_pool"
CONTOUR_B_RERANK_MAX_LENGTH = 256
