"""Перебор на dev-70. Holdout-3 не читается.

Реранкер только с пулом 12. ``choose_default`` оставляет варианты с hit@5 ≥ 0.9
и берёт среди них максимальный nDCG@8. ``prompt_pool`` в сетке есть, но при
просадке hit он не выбирается.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.orchestrator.holdout import FAMILY_NODES, HoldoutLeakError, is_holdout_id, load_dev_expanded

RERANK_POOL = 12
FOCUS = ("camera", "action", "script")
GRID_PATH = Path(__file__).resolve().parent / "datasets" / "precision_live_dev70_grid.json"
DIAGNOSIS_PATH = Path(__file__).resolve().parent / "datasets" / "precision_live_dev70_diagnosis.json"

# Пул реранкера зафиксирован. Веса — слияние BM25 и реранкера, не лексика/вектор RRF.
GRID: tuple[dict[str, Any], ...] = (
    {
        "key": "none-rrf-0.5-1.5",
        "aggregate": "none",
        "rerank": True,
        "fusion_bm25": 0.5,
        "fusion_rerank": 1.5,
    },
    {
        "key": "prompt-expand-rrf-0.5-1.5",
        "aggregate": "prompt_expand",
        "rerank": True,
        "fusion_bm25": 0.5,
        "fusion_rerank": 1.5,
    },
    {
        "key": "prompt-pool-rrf-0.5-1.5",
        "aggregate": "prompt_pool",
        "rerank": True,
        "fusion_bm25": 0.5,
        "fusion_rerank": 1.5,
    },
    {
        "key": "none-no-rerank",
        "aggregate": "none",
        "rerank": False,
        "fusion_bm25": 0.5,
        "fusion_rerank": 1.5,
    },
)


def load_dev70() -> list[dict[str, Any]]:
    """70 запросов dev. Идентификатор holdout-3 — ошибка, не фильтр."""
    queries = load_dev_expanded()
    leaked = [str(query["id"]) for query in queries if is_holdout_id(str(query["id"]))]
    if leaked:
        raise HoldoutLeakError("сетка видит holdout-3: " + ", ".join(leaked))
    if len(queries) != 70:
        raise RuntimeError(f"dev-70: ожидалось 70 запросов, получилось {len(queries)}")
    return queries


def family_of_source(source_id: str) -> str | None:
    for family, nodes in FAMILY_NODES.items():
        if source_id in nodes:
            return family
    return None


def channel_ahead(displacer: dict[str, float], relevant: dict[str, float] | None) -> str:
    """Почему чужой чанк стоит выше первого релевантного: BM25, вектор или слияние."""
    if relevant is None:
        return "no_relevant_in_top"
    bm25_ahead = float(displacer.get("score_bm25") or 0) > float(relevant.get("score_bm25") or 0)
    vec_ahead = float(displacer.get("score_vec") or 0) > float(relevant.get("score_vec") or 0)
    if bm25_ahead and vec_ahead:
        return "bm25_and_vector"
    if bm25_ahead:
        return "bm25"
    if vec_ahead:
        return "vector"
    return "fusion_or_rerank"


def explain_displacement(family: str, top: list[dict[str, Any]]) -> dict[str, Any]:
    """Чанки выше первого релевантного и канал, по которому они его обходят."""
    first = next((index for index, item in enumerate(top) if item.get("relevant")), None)
    relevant = None if first is None else top[first]
    ahead: list[dict[str, Any]] = []
    for item in top[: first if first is not None else len(top)]:
        owner = family_of_source(str(item.get("source_id") or ""))
        if item.get("corpus") == "rules":
            kind = "rules"
        elif owner == family:
            kind = "same_family"
        elif owner:
            kind = owner
        else:
            kind = "outside"
        ahead.append(
            {
                "source_id": item.get("source_id"),
                "part_key": item.get("part_key"),
                "corpus": item.get("corpus"),
                "title": item.get("title"),
                "owner": kind,
                "channel": channel_ahead(item, relevant),
            }
        )
    return {
        "first_relevant_rank": None if first is None else first + 1,
        "ahead": ahead,
    }


def assert_grid_pool() -> None:
    if RERANK_POOL != 12:
        raise RuntimeError("реранкер сетки только top-12")
    for spec in GRID:
        if spec.get("rerank_pool", RERANK_POOL) != 12:
            raise RuntimeError(f"{spec['key']}: пул реранкера не 12")


def grid_runs_for_choice(
    rows: list[dict[str, Any]],
    *,
    prod_p95_s: float,
) -> list[tuple[str, str, dict[str, float]]]:
    """В ``choose_default`` уходит p95 прод-пути, не p95 fp32-эвала.

    Вариант без реранкера получает тот же потолок: он не медленнее прод-пути с реранкером.
    Если прод-путь с реранкером выше 1.2 с, реранкер из выбора выпадает.
    """
    runs: list[tuple[str, str, dict[str, float]]] = []
    for row in rows:
        uses_rerank = bool(row["rerank"])
        p95 = float(prod_p95_s) if uses_rerank else min(float(prod_p95_s), 1.2)
        if uses_rerank and float(prod_p95_s) > 1.2:
            p95 = float(prod_p95_s)
        runs.append(
            (
                str(row["key"]),
                str(row["key"]),
                {
                    "hit_at_5": float(row["hit_at_5"]),
                    "ndcg_at_8": float(row["ndcg_at_8"]),
                    "p95_s": p95,
                },
            )
        )
    return runs


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _top_row(chunk: Any, relevant: bool) -> dict[str, Any]:
    return {
        "relevant": relevant,
        "corpus": chunk.corpus,
        "source_id": chunk.source_id,
        "part_key": chunk.metadata.get("part_key"),
        "title": chunk.title,
        "score_bm25": float(chunk.score_bm25 or 0.0),
        "score_vec": float(chunk.score_vec or 0.0),
    }


def run_dev70_grid(out: Path | None = None, diagnosis_out: Path | None = None) -> dict[str, Any]:
    """Один fp32-индекс, сетка retrieve. Holdout-3 не открывается."""
    import os
    import shutil
    import tempfile
    import time
    from pathlib import Path as FsPath

    from evals.orchestrator.precision_live import (
        _doc_ndcg,
        _engine,
        _is_relevant_transferred,
        _ndcg_at_8,
        _percentile,
        _relevant_source_ids,
        family_slices,
        summarize_rows,
    )
    from app.orchestrator.rag.embedder import build_embedder
    from app.orchestrator.rag.rerank import build_reranker

    assert_grid_pool()
    queries = load_dev70()
    temp_dir = FsPath(tempfile.mkdtemp(prefix="orch-rag-dev70-"))
    db_path = temp_dir / "orch_rag.db"
    embedder = build_embedder("e5-large", eval_mode=True)
    reranker = build_reranker("bge-m3", max_length=256, eval_mode=True)
    engine = _engine(db_path, embedder, reranker)
    configs: list[dict[str, Any]] = []
    diagnosis: list[dict[str, Any]] = []
    try:
        print("dev70 reindex", os.getpid(), flush=True)
        engine.reindex(["rules", "nodes", "prompts"], timeout=900)
        indexed = engine.store.fetch_chunks(["rules", "nodes", "prompts"])
        for chunk in indexed:
            chunk.metadata.pop("_embedding", None)
        print("dev70 chunks", len(indexed), flush=True)
        indexed_source_ids = {chunk.source_id for chunk in indexed}
        rule_source_ids = {chunk.source_id for chunk in indexed if chunk.corpus == "rules"}
        relevant_counts = [
            sum(1 for chunk in indexed if _is_relevant_transferred(chunk, query)) for query in queries
        ]
        for spec in GRID:
            kwargs = {
                "rerank": bool(spec["rerank"]),
                "rrf_k": 20,
                "rrf_weights": (0.5, 1.5),
                "bm25_scope": "full",
                "rerank_pool": RERANK_POOL,
                "aggregate": spec["aggregate"],
                "rerank_fusion": "rrf",
                "rerank_fusion_weights": (float(spec["fusion_bm25"]), float(spec["fusion_rerank"])),
                "bm25_protect": 0.0,
                "family_boost": 1.0,
            }
            engine.retrieve(queries[0]["text"], ["rules", "nodes", "prompts"], top_k=8, **kwargs)
            rows: list[dict[str, Any]] = []
            latencies: list[float] = []
            for index, (query, relevant_n) in enumerate(zip(queries, relevant_counts, strict=True), start=1):
                started = time.perf_counter()
                found = engine.retrieve(query["text"], ["rules", "nodes", "prompts"], top_k=8, **kwargs)
                elapsed = time.perf_counter() - started
                latencies.append(elapsed)
                flags = [_is_relevant_transferred(chunk, query) for chunk in found]
                top = [_top_row(chunk, flag) for chunk, flag in zip(found, flags, strict=True)]
                if query.get("family") == "rules":
                    sources = set(rule_source_ids)
                else:
                    sources = _relevant_source_ids(query, literal=True)
                row = {
                    "id": query["id"],
                    "family": query.get("family"),
                    "hit5": any(flags[:5]),
                    "ndcg": _ndcg_at_8(flags, relevant_n),
                    "ndcg_doc": _doc_ndcg(found, sources, indexed_source_ids),
                    "latency_s": elapsed,
                }
                rows.append(row)
                if index % 10 == 0:
                    print(spec["key"], index, "/", len(queries), flush=True)
                if spec["key"] == "none-rrf-0.5-1.5" and query.get("family") in FOCUS:
                    explained = explain_displacement(str(query["family"]), top)
                    diagnosis.append(
                        {
                            "id": query["id"],
                            "family": query["family"],
                            "text": query["text"],
                            "hit5": row["hit5"],
                            "ndcg": row["ndcg"],
                            **explained,
                        }
                    )
            summary = summarize_rows(rows)
            summary["p95_s"] = _percentile(latencies, 95)
            entry = {
                "key": spec["key"],
                "aggregate": spec["aggregate"],
                "rerank": bool(spec["rerank"]),
                "rerank_pool": RERANK_POOL,
                "fusion_bm25": spec["fusion_bm25"],
                "fusion_rerank": spec["fusion_rerank"],
                **summary,
                "slices": family_slices(rows),
            }
            configs.append(entry)
            print(
                spec["key"],
                "hit",
                round(summary["hit_at_5"], 4),
                "ndcg",
                round(summary["ndcg_at_8"], 4),
                "p95",
                round(summary["p95_s"], 3),
                flush=True,
            )
            write_json(out or GRID_PATH, {"set": "dev-70", "rerank_pool": RERANK_POOL, "configs": configs})
            if diagnosis:
                write_json(
                    diagnosis_out or DIAGNOSIS_PATH,
                    {"set": "dev-70", "config": "none-rrf-0.5-1.5", "queries": diagnosis},
                )
    finally:
        engine.close()
        shutil.rmtree(temp_dir, ignore_errors=True)
    payload = {"set": "dev-70", "rerank_pool": RERANK_POOL, "configs": configs}
    write_json(out or GRID_PATH, payload)
    write_json(
        diagnosis_out or DIAGNOSIS_PATH,
        {"set": "dev-70", "config": "none-rrf-0.5-1.5", "queries": diagnosis},
    )
    return payload


def main() -> None:
    run_dev70_grid()


if __name__ == "__main__":
    main()
