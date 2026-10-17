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

Отбор дефолта по новой разметке (перенос заморожен в ``1396ea43``):
среди вариантов с hit@5 ≥ 0.9 и p95 ≤ 1.5 с берётся максимальный nDCG@8.
Порог nDCG@8 0.65 не снижался.

Прогон после переноса: оба варианта дали hit@5 0.8200 и nDCG@8 0.4574.
hit@5 ≥ 0.9 не взят, поэтому принятого дефолта нет.
Сырой nDCG bge-m3 выше на 0.00002, на четырёх знаках это 0.4574.
В отбор он не входит и не возвращён. Порог 0.65 тоже не взят.

Константы ниже — проводка гибрида, а не прошедший отбор:
e5-large без реранкера, RRF k=20, веса лексика/вектор 0.5/1.5, BM25 по всему тексту.
Пул 20 и 256 токенов оставлены как замер bge-m3.
Цифры — в ``evals/orchestrator/datasets/precision_live_report.md``.
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
