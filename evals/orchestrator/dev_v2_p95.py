"""Три холодных p95 прод-пути на победителе dev-v2.

Профиль уже должен совпадать с выбранной строкой.
Квантованный реранкер, корпуса rules/history/nodes/prompts, кэш сброшен.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.orchestrator.rag.profile import (
    CONTOUR_B_AGGREGATE,
    CONTOUR_B_BM25_PROTECT,
    CONTOUR_B_BM25_SCOPE,
    CONTOUR_B_FAMILY_BOOST,
    CONTOUR_B_FUSION_BM25,
    CONTOUR_B_FUSION_RERANK,
    CONTOUR_B_RERANK_FUSION,
    CONTOUR_B_RERANK_POOL,
    CONTOUR_B_RRF_K,
    CONTOUR_B_RRF_LEXICAL,
    CONTOUR_B_RRF_VECTOR,
)
from evals.orchestrator.dev_v2_grid import GRID_PATH, load_dev_v2

PASS_COUNT = 3


def profile_view() -> dict[str, Any]:
    return {
        "aggregate": CONTOUR_B_AGGREGATE,
        "rerank": True,
        "rerank_pool": CONTOUR_B_RERANK_POOL,
        "fusion_bm25": float(CONTOUR_B_FUSION_BM25),
        "fusion_rerank": float(CONTOUR_B_FUSION_RERANK),
        "rrf_k": int(CONTOUR_B_RRF_K),
        "lexical": float(CONTOUR_B_RRF_LEXICAL),
        "vector": float(CONTOUR_B_RRF_VECTOR),
        "family_boost": float(CONTOUR_B_FAMILY_BOOST),
        "bm25_protect": float(CONTOUR_B_BM25_PROTECT),
        "bm25_scope": CONTOUR_B_BM25_SCOPE,
        "rerank_fusion": CONTOUR_B_RERANK_FUSION,
    }


def profile_matches(row: dict[str, Any]) -> bool:
    """Строка сетки и константы профиля задают один и тот же retrieve."""
    view = profile_view()
    if not bool(row["rerank"]):
        return False
    for key in (
        "aggregate",
        "rerank_pool",
        "rrf_k",
        "bm25_scope",
        "rerank_fusion",
    ):
        if row.get(key, view[key]) != view[key]:
            return False
    for key in ("fusion_bm25", "fusion_rerank", "lexical", "vector", "family_boost", "bm25_protect"):
        if abs(float(row[key]) - float(view[key])) > 1e-9:
            return False
    return True


def run_prod_p95(grid_path: Path | None = None) -> list[float]:
    """Три холодных p95. Второй ONNX-процесс рядом запускать нельзя."""
    import os

    import app.orchestrator.rag as rag_mod
    from app.orchestrator.rag import get_engine, reset_engine
    from evals.orchestrator.precision_live import measure_chunk_rows, summarize_rows

    path = grid_path or GRID_PATH
    document = json.loads(path.read_text(encoding="utf-8"))
    chosen_key = document.get("chosen_key")
    rows = [row for row in document["configs"] if row["key"] == chosen_key]
    if len(rows) != 1:
        raise RuntimeError(f"в сетке нет единственного победителя {chosen_key!r}")
    if not profile_matches(rows[0]):
        raise RuntimeError(f"профиль не совпал с {chosen_key}")
    queries = load_dev_v2()
    prod_dir = Path(tempfile.mkdtemp(prefix="orch-rag-dev100-prod-"))
    rag_mod.default_db_path = lambda: prod_dir / "orch_rag.db"
    reset_engine()
    prod = get_engine(profile="contour-b")
    defaults = {key: value for key, value in prod.retrieve_defaults.items() if value is not None}
    corpora = ["rules", "history", "nodes", "prompts"]
    samples: list[float] = []
    try:
        print("dev100 prod reindex", os.getpid(), flush=True)
        prod.reindex(corpora, timeout=900)
        for index in range(PASS_COUNT):
            prod._clear_retrieval_cache()
            prod_rows = measure_chunk_rows(
                prod,
                queries,
                rerank=True,
                rrf_k=int(defaults["rrf_k"]),
                rrf_weights=tuple(defaults["rrf_weights"]),
                bm25_scope=str(defaults["bm25_scope"]),
                rerank_pool=int(defaults["rerank_pool"]),
                aggregate=str(defaults["aggregate"]),
                rerank_fusion=str(defaults["rerank_fusion"]),
                rerank_fusion_weights=tuple(defaults["rerank_fusion_weights"]),
                bm25_protect=float(defaults["bm25_protect"]),
                family_boost=float(defaults["family_boost"]),
                corpora=corpora,
            )
            samples.append(float(summarize_rows(prod_rows)["p95_s"]))
            print("DEV100_PROD_PASS", index, samples[-1], flush=True)
    finally:
        reset_engine()
        shutil.rmtree(prod_dir, ignore_errors=True)
    document["prod_p95_s"] = samples
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return samples


def main() -> None:
    print(run_prod_p95(), flush=True)


if __name__ == "__main__":
    main()
