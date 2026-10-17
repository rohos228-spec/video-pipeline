"""RAG оркестратора: ``retrieve`` / ``reindex``.

Корпуса: ``rules`` | ``history`` | ``nodes`` | ``prompts``.
Хранилище по умолчанию: ``data/orch_rag.db`` (WAL, один reindex-воркер).
"""

from __future__ import annotations

import os
from pathlib import Path

from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.types import Chunk, ReindexResult

_engine: RagEngine | None = None


def default_db_path() -> Path:
    from app.settings import settings

    return Path(settings.data_dir) / "orch_rag.db"


def _rerank_disabled() -> bool:
    return os.environ.get("ORCH_RAG_RERANK", "1").strip().lower() in {"0", "false", "off", "no", "none"}


def _engine_for_profile(profile: str) -> RagEngine:
    """``contour-b`` подключает embedder/реранкер и дефолты retrieve.

    ``ORCH_RAG_RERANK=0`` оставляет гибрид без реранкера. Любой другой профиль,
    включая пустой, — hashing без весов: так CI не качает модели.
    """
    if profile != "contour-b":
        return RagEngine(db_path=default_db_path())
    from app.orchestrator.rag.embedder import build_embedder
    from app.orchestrator.rag.profile import (
        CONTOUR_B_AGGREGATE,
        CONTOUR_B_BM25_SCOPE,
        CONTOUR_B_EMBEDDER,
        CONTOUR_B_RERANK_MAX_LENGTH,
        CONTOUR_B_RERANK_POOL,
        CONTOUR_B_RERANKER,
        CONTOUR_B_RRF_K,
        CONTOUR_B_RRF_LEXICAL,
        CONTOUR_B_RRF_VECTOR,
    )
    from app.orchestrator.rag.rerank import build_reranker

    reranker = None
    if not _rerank_disabled() and CONTOUR_B_RERANKER not in {"", "none", "off"}:
        reranker = build_reranker(CONTOUR_B_RERANKER, max_length=CONTOUR_B_RERANK_MAX_LENGTH)
    engine = RagEngine(
        db_path=default_db_path(),
        embedder=build_embedder(CONTOUR_B_EMBEDDER),
        reranker=reranker,
    )
    engine.retrieve_defaults = {
        "rrf_k": CONTOUR_B_RRF_K,
        "rrf_weights": (CONTOUR_B_RRF_LEXICAL, CONTOUR_B_RRF_VECTOR),
        "bm25_scope": CONTOUR_B_BM25_SCOPE,
        "rerank_pool": CONTOUR_B_RERANK_POOL,
        "aggregate": CONTOUR_B_AGGREGATE,
        "rerank": False if _rerank_disabled() else None,
    }
    return engine


def get_engine(*, profile: str | None = None) -> RagEngine:
    """Синглтон. ``profile`` или ``ORCH_RAG_PROFILE=contour-b`` включает профиль B.

    Повторный вызов не пересобирает движок: сначала ``reset_engine()``.
    """
    global _engine
    if _engine is None:
        chosen = profile if profile is not None else os.environ.get("ORCH_RAG_PROFILE", "")
        _engine = _engine_for_profile(chosen.strip())
    return _engine


def reset_engine() -> None:
    """Сброс процесса-синглтона (тесты)."""
    global _engine
    if _engine is not None:
        _engine.close()
        _engine = None


def retrieve(
    query: str,
    corpora: list[str] | tuple[str, ...] | None = None,
    top_k: int = 8,
    **kwargs: object,
) -> list[Chunk]:
    engine = get_engine()
    merged = {**getattr(engine, "retrieve_defaults", {}), **kwargs}
    return engine.retrieve(query, corpora, top_k, **merged)


def reindex(corpus: str | None = None) -> ReindexResult | dict[str, ReindexResult]:
    return get_engine().reindex(corpus)


def rules_system_block() -> str:
    return get_engine().rules_system_block()


__all__ = [
    "Chunk",
    "RagEngine",
    "ReindexResult",
    "get_engine",
    "reindex",
    "reset_engine",
    "retrieve",
    "rules_system_block",
]
