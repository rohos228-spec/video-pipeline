"""Явный профиль контура B. Сам по себе веса не грузит.

``get_engine()`` и CI остаются на hashing без реранкера.
Включить профиль можно только вручную::

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
    )

Правило: лучший по nDCG@8 среди вариантов с p95 ≤ 1.5 с.
Если реранкер не поднимает nDCG выше гибрида e5-large, дефолт — этот гибрид.
Прогон контура B: реранкер помогает, поэтому дефолт —
e5-large + bge-m3, пул 20, 256 токенов, RRF k=20, веса лексика/вектор 0.5/1.5,
BM25 по всему тексту. Константы совпадают с
``evals/orchestrator/datasets/precision_live_report.md``.
"""

from __future__ import annotations

# Обновляется вместе с отчётом контура B, не подставляется в get_engine().
CONTOUR_B_EMBEDDER = "e5-large"
CONTOUR_B_RERANKER = "bge-m3"
CONTOUR_B_RRF_K = 20
CONTOUR_B_RRF_LEXICAL = 0.5
CONTOUR_B_RRF_VECTOR = 1.5
CONTOUR_B_BM25_SCOPE = "full"
CONTOUR_B_RERANK_POOL = 20
CONTOUR_B_RERANK_MAX_LENGTH = 256
