"""Эксперимент: секции промптов и агрегация по source_id.

Не пишет запросы и не меняет отчёт контура B. Живых вызовов провайдера нет.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from app.orchestrator.rag.embedder import build_embedder
from app.orchestrator.rag.rerank import build_reranker
from evals.orchestrator.precision_live import _engine, build_queries, measure

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    queries = build_queries()
    embedder = build_embedder("e5-large")
    temp_dir = Path(tempfile.mkdtemp(prefix="orch-rag-sections-"))
    engine = _engine(temp_dir / "orch_rag.db", embedder, None)
    try:
        engine.reindex(["rules", "nodes", "prompts"])
        chunks = engine.store.fetch_chunks(["rules", "nodes", "prompts"])
        print("chunks", len(chunks), flush=True)
        base = {
            "rrf_k": 20,
            "rrf_weights": (0.5, 1.5),
            "bm25_scope": "full",
            "rerank_pool": 20,
        }
        rows: list[tuple[str, dict[str, float]]] = []
        for mode in ("max", "sum2"):
            for cap in (2, 3, 4):
                label = f"e5-large гибрид {mode} cap={cap}"
                metrics, _details = measure(
                    queries,
                    engine,
                    rerank=False,
                    source_aggregate=mode,
                    source_cap=cap,
                    **base,
                )
                rows.append((label, metrics))
                print(label, _fmt(metrics), flush=True)
        eligible = [row for row in rows if row[1]["hit_at_5"] >= 0.96]
        pool = eligible or rows
        best = sorted(pool, key=lambda row: row[1]["ndcg_at_8"], reverse=True)[:2]
        print("rerank bases", [row[0] for row in best], flush=True)
    finally:
        engine.close()

    reranker = build_reranker("bge-m3", max_length=256)
    rerank_engine = _engine(temp_dir / "orch_rag.db", embedder, reranker)
    try:
        rerank_engine.reindex(["rules", "nodes", "prompts"])
        for label, _metrics in best:
            mode = "sum2" if "sum2" in label else "max"
            cap = int(label.rsplit("=", 1)[1])
            rerank_label = f"{label} + bge-m3 топ-20 256"
            metrics, _details = measure(
                queries,
                rerank_engine,
                rerank=True,
                source_aggregate=mode,
                source_cap=cap,
                **base,
            )
            rows.append((rerank_label, metrics))
            print(rerank_label, _fmt(metrics), flush=True)
    finally:
        rerank_engine.close()

    print("---")
    for label, metrics in rows:
        print(
            f"| {label} | {metrics['hit_at_5']:.4f} | {metrics['ndcg_at_8']:.4f} | "
            f"{metrics['p50_s']:.3f} | {metrics['p95_s']:.3f} |",
            flush=True,
        )


def _fmt(metrics: dict[str, float]) -> dict[str, float]:
    return {key: round(value, 4) for key, value in metrics.items()}


if __name__ == "__main__":
    main()
