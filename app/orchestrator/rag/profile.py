"""Явный профиль контура B. Сам по себе веса не грузит.

``get_engine()`` и CI остаются на hashing без реранкера.
Включить профиль можно только вручную::

    RagEngine(
        embedder=build_embedder(CONTOUR_B_EMBEDDER),
        reranker=build_reranker(CONTOUR_B_RERANKER) if CONTOUR_B_RERANKER != "none" else None,
    )

Правило куратора: если p95 у e5-large + реранкер больше 1.5 с, дефолт —
e5-small + реранкер; иначе лучший по hit@5, затем nDCG@8.
Константы ниже совпадают с последним прогоном
``evals/orchestrator/datasets/precision_live_report.md``.
"""

from __future__ import annotations

# Обновляется вместе с отчётом контура B, не подставляется в get_engine().
CONTOUR_B_EMBEDDER = "e5-small"
CONTOUR_B_RERANKER = "bge"
