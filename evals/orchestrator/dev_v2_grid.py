"""Перебор на dev-v2 (dev-100). Holdout-5 не открывается.

Сетка dev-70 целиком, плюс family_boost и защита топ-1 BM25 на текущем
слиянии expand_tail 0.25/1.5 k=20: эти два множителя стоят после реранкера
и не требуют нового прохода cross-encoder.

Отбор: hit@5 ≥ 0.9 обязателен, затем максимальный nDCG@8. p95 fp32 в отбор
не входит. Цифры только dev-70 пишутся рядом, чтобы видеть регрессию.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evals.orchestrator.dev70_grid import GRID as DEV70_GRID
from evals.orchestrator.dev70_grid import RERANK_POOL, grid_sort_key
from evals.orchestrator.holdout import HoldoutLeakError, dev_plus_ids, is_holdout_id

GRID_PATH = Path(__file__).resolve().parent / "datasets" / "precision_live_dev100_grid.json"
HIT_AT_5_MIN = 0.9

_BASELINE_KEY = "expand-tail-rrf-0.25-1.5-k20"


def _extra(
    key: str,
    *,
    family_boost: float = 1.0,
    bm25_protect: float = 0.0,
) -> dict[str, Any]:
    return {
        "key": key,
        "aggregate": "expand_tail",
        "rerank": True,
        "fusion_bm25": 0.25,
        "fusion_rerank": 1.5,
        "rrf_k": 20,
        "lexical": 0.5,
        "vector": 1.5,
        "family_boost": family_boost,
        "bm25_protect": bm25_protect,
    }


EXTRAS: tuple[dict[str, Any], ...] = (
    _extra("expand-tail-rrf-0.25-1.5-k20-fam1.5", family_boost=1.5),
    _extra("expand-tail-rrf-0.25-1.5-k20-fam2", family_boost=2.0),
    _extra("expand-tail-rrf-0.25-1.5-k20-protect1.2", bm25_protect=1.2),
    _extra("expand-tail-rrf-0.25-1.5-k20-protect2", bm25_protect=2.0),
)

GRID: tuple[dict[str, Any], ...] = tuple(DEV70_GRID) + EXTRAS


def pending_specs(done: set[str]) -> list[dict[str, Any]]:
    """Сначала порядок dev-70, затем дешёвые добавки, когда кэш реранкера уже тёплый."""
    base = sorted((spec for spec in DEV70_GRID if spec["key"] not in done), key=grid_sort_key)
    extra = [spec for spec in EXTRAS if spec["key"] not in done]
    return [*base, *extra]


def load_dev_v2() -> list[dict[str, Any]]:
    """100 запросов: dev-70 и d2-01..30. Идентификатор holdout — ошибка."""
    from evals.orchestrator.dev_v2 import load_dev100

    queries = load_dev100()
    leaked = [str(query["id"]) for query in queries if is_holdout_id(str(query["id"]))]
    if leaked:
        raise HoldoutLeakError("сетка dev-v2 видит holdout: " + ", ".join(leaked))
    if len(queries) != 100:
        raise RuntimeError(f"dev-v2: ожидалось 100 запросов, получилось {len(queries)}")
    return queries


def choose_dev_v2(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """hit@5 ≥ 0.9 обязателен, затем максимальный nDCG@8.

    p95, в том числе fp32, отбор не решает. При равном nDCG берётся больший hit@5.
    Полное равенство оставляет прежний дефолт dev-70, если он в списке.
    Цифры dev-70 в ключе нет.
    """
    eligible = [row for row in rows if float(row["hit_at_5"]) >= HIT_AT_5_MIN]
    if not eligible:
        return None

    def sort_key(row: dict[str, Any]) -> tuple[float, float, int]:
        keep = 1 if str(row["key"]) == _BASELINE_KEY else 0
        return (float(row["ndcg_at_8"]), float(row["hit_at_5"]), keep)

    return max(eligible, key=sort_key)


def _spec_fields(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": spec["key"],
        "aggregate": spec["aggregate"],
        "rerank": bool(spec["rerank"]),
        "rerank_pool": RERANK_POOL,
        "fusion_bm25": float(spec["fusion_bm25"]),
        "fusion_rerank": float(spec["fusion_rerank"]),
        "rrf_k": int(spec.get("rrf_k", 20)),
        "lexical": float(spec.get("lexical", 0.5)),
        "vector": float(spec.get("vector", 1.5)),
        "family_boost": float(spec.get("family_boost", 1.0)),
        "bm25_protect": float(spec.get("bm25_protect", 0.0)),
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_dev_v2_grid(out: Path | None = None) -> dict[str, Any]:
    """Один fp32-индекс. Holdout не читается. Метрики dev-100 и срез dev-70."""
    import os
    import shutil
    import tempfile
    import time
    from pathlib import Path as FsPath

    from app.orchestrator.rag.embedder import build_embedder
    from app.orchestrator.rag.rerank import build_reranker
    from evals.orchestrator.dev70_grid import _CachingReranker
    from evals.orchestrator.precision_live import (
        _doc_ndcg,
        _engine,
        _is_relevant_transferred,
        _ndcg_at_8,
        _relevant_source_ids,
        family_slices,
        summarize_rows,
    )

    if RERANK_POOL != 12:
        raise RuntimeError("реранкер сетки только top-12")
    queries = load_dev_v2()
    dev70_ids = set(dev_plus_ids())
    destination = out or GRID_PATH
    existing: list[dict[str, Any]] = []
    if destination.is_file():
        existing = list(json.loads(destination.read_text(encoding="utf-8")).get("configs") or [])
    done = {str(row["key"]) for row in existing}
    pending = pending_specs(done)
    temp_dir = FsPath(tempfile.mkdtemp(prefix="orch-rag-dev100-"))
    embedder = build_embedder("e5-large", eval_mode=True)
    reranker = _CachingReranker(build_reranker("bge-m3", max_length=256, eval_mode=True))
    engine = _engine(temp_dir / "orch_rag.db", embedder, reranker)
    configs: list[dict[str, Any]] = list(existing)
    try:
        print("dev100 reindex", os.getpid(), flush=True)
        engine.reindex(["rules", "nodes", "prompts"], timeout=900)
        indexed = engine.store.fetch_chunks(["rules", "nodes", "prompts"])
        for chunk in indexed:
            chunk.metadata.pop("_embedding", None)
        print("dev100 chunks", len(indexed), "pending", len(pending), flush=True)
        indexed_source_ids = {chunk.source_id for chunk in indexed}
        relevant_counts = [
            sum(1 for chunk in indexed if _is_relevant_transferred(chunk, query)) for query in queries
        ]
        for spec in pending:
            fields = _spec_fields(spec)
            kwargs = {
                "rerank": fields["rerank"],
                "rrf_k": fields["rrf_k"],
                "rrf_weights": (fields["lexical"], fields["vector"]),
                "bm25_scope": "full",
                "rerank_pool": RERANK_POOL,
                "aggregate": fields["aggregate"],
                "rerank_fusion": "rrf",
                "rerank_fusion_weights": (fields["fusion_bm25"], fields["fusion_rerank"]),
                "bm25_protect": fields["bm25_protect"],
                "family_boost": fields["family_boost"],
            }
            engine.retrieve(queries[0]["text"], ["rules", "nodes", "prompts"], top_k=8, **kwargs)
            rows: list[dict[str, Any]] = []
            for index, (query, relevant_n) in enumerate(zip(queries, relevant_counts), start=1):
                started = time.perf_counter()
                found = engine.retrieve(query["text"], ["rules", "nodes", "prompts"], top_k=8, **kwargs)
                elapsed = time.perf_counter() - started
                flags = [_is_relevant_transferred(chunk, query) for chunk in found]
                sources = _relevant_source_ids(query, literal=True)
                rows.append(
                    {
                        "id": query["id"],
                        "family": query.get("family"),
                        "hit5": any(flags[:5]),
                        "ndcg": _ndcg_at_8(flags, relevant_n),
                        "ndcg_doc": _doc_ndcg(found, sources, indexed_source_ids),
                        "latency_s": elapsed,
                    }
                )
                if index % 25 == 0:
                    print(fields["key"], index, "/", len(queries), flush=True)
            dev70_rows = [row for row in rows if row["id"] in dev70_ids]
            if len(dev70_rows) != 70:
                raise RuntimeError(f"{fields['key']}: срез dev-70 = {len(dev70_rows)}")
            summary = summarize_rows(rows)
            dev70 = summarize_rows(dev70_rows)
            entry = {
                **fields,
                "hit_at_5": summary["hit_at_5"],
                "ndcg_at_8": summary["ndcg_at_8"],
                "ndcg_doc": summary["ndcg_doc"],
                "n": summary["n"],
                "p95_fp32_s": summary["p95_s"],
                "slices": family_slices(rows),
                "dev70_hit_at_5": dev70["hit_at_5"],
                "dev70_ndcg_at_8": dev70["ndcg_at_8"],
                "dev70_n": dev70["n"],
                "dev70_slices": family_slices(dev70_rows),
            }
            configs.append(entry)
            print(
                fields["key"],
                "hit",
                round(summary["hit_at_5"], 4),
                "ndcg",
                round(summary["ndcg_at_8"], 4),
                "dev70",
                round(dev70["hit_at_5"], 4),
                round(dev70["ndcg_at_8"], 4),
                flush=True,
            )
            payload = _payload(configs)
            write_json(destination, payload)
    finally:
        engine.close()
        shutil.rmtree(temp_dir, ignore_errors=True)
    payload = _payload(configs)
    write_json(destination, payload)
    return payload


def _payload(configs: list[dict[str, Any]]) -> dict[str, Any]:
    body: dict[str, Any] = {
        "set": "dev-100",
        "rerank_pool": RERANK_POOL,
        "rule": "hit@5 >= 0.9, then max ndcg@8",
        "configs": configs,
    }
    if len(configs) == len(GRID):
        chosen = choose_dev_v2(configs)
        body["chosen_key"] = None if chosen is None else chosen["key"]
    return body


def main() -> None:
    run_dev_v2_grid()


if __name__ == "__main__":
    main()
