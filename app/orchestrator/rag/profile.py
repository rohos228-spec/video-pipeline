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

Правило: лучший по nDCG@8 среди вариантов с p95 ≤ 1.5 с.
Если реранкер не поднимает nDCG выше гибрида e5-large, дефолт — этот гибрид.
После ``split_prompt(max_parts=3)`` bge-m3 топ-20/256 nDCG не поднял
(0.5258 против 0.5444 у гибрида), поэтому дефолт —
e5-large без реранкера, RRF k=20, веса лексика/вектор 0.5/1.5, BM25 по всему тексту.
Пул 20 и 256 токенов оставлены как замер отклонённого bge-m3.
Константы совпадают с ``evals/orchestrator/datasets/precision_live_report.md``.
Порог nDCG@8 0.65 временный (пересмотр на R5); на этом прогоне он не взят.
"""

from __future__ import annotations

# Обновляется вместе с отчётом контура B. get_engine() читает их только при profile=contour-b.
CONTOUR_B_EMBEDDER = "e5-large"
CONTOUR_B_RERANKER = "none"
CONTOUR_B_RRF_K = 20
CONTOUR_B_RRF_LEXICAL = 0.5
CONTOUR_B_RRF_VECTOR = 1.5
CONTOUR_B_BM25_SCOPE = "full"
CONTOUR_B_RERANK_POOL = 20
CONTOUR_B_RERANK_MAX_LENGTH = 256
