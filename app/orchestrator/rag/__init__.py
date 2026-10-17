"""RAG оркестратора: ``retrieve`` / ``reindex``.

Корпуса: ``rules`` | ``history`` | ``nodes`` | ``prompts``.
Хранилище по умолчанию: ``data/orch_rag.db`` (WAL, один reindex-воркер).
"""

from __future__ import annotations

from pathlib import Path

from app.orchestrator.rag.engine import RagEngine
from app.orchestrator.rag.types import Chunk, ReindexResult

_engine: RagEngine | None = None


def default_db_path() -> Path:
    from app.settings import settings

    return Path(settings.data_dir) / "orch_rag.db"


def get_engine() -> RagEngine:
    global _engine
    if _engine is None:
        _engine = RagEngine(db_path=default_db_path())
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
) -> list[Chunk]:
    return get_engine().retrieve(query, corpora, top_k)


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
