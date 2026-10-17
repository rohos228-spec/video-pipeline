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
        rerank=False,  # гибрид без реранкера
    )

Отбор дефолта — только вариант C (8 частей, метки s0–s7):
среди hit@5 ≥ 0.9 и p95 ≤ 1.5 с берётся максимальный nDCG@8.
Порог nDCG@8 0.65 не снижался.

Вариант C, bge-m3 топ-20/256: hit@5 0.9400, nDCG@8 0.6235, p95 1.491 с.
Гибрид C в отбор не вошёл: hit@5 0.8800. Реранкер возвращён.
Порог 0.65 не взят (0.6235) и не снижался.

Константы: e5-large, bge-m3, RRF k=20, веса 0.5/1.5, BM25 full,
пул 20, 256 токенов. ``ORCH_RAG_RERANK=0`` или ``retrieve(..., rerank=False)``
оставляют гибрид. Цифры — в ``evals/orchestrator/datasets/precision_live_report.md``.
"""

from __future__ import annotations

# Обновляется вместе с отчётом контура B. get_engine() читает их только при profile=contour-b.
CONTOUR_B_EMBEDDER = "e5-large"
CONTOUR_B_RERANKER = "bge-m3"
CONTOUR_B_RRF_K = 20
CONTOUR_B_RRF_LEXICAL = 0.5
CONTOUR_B_RRF_VECTOR = 1.5
CONTOUR_B_BM25_SCOPE = "full"
CONTOUR_B_RERANK_POOL = 20
CONTOUR_B_RERANK_MAX_LENGTH = 256
