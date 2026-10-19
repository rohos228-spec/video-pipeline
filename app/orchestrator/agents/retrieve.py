"""Общий retrieve для planner и critic.

Чужой RAG не импортируется. Синхронный мок тоже принимается.
"""

from __future__ import annotations

import inspect
from typing import Any

from app.orchestrator.contracts import Chunk


async def retrieve(rag: Any, query: str, corpora: list[str], top_k: int) -> list[Chunk]:
    try:
        result = rag.retrieve(query, corpora, top_k)
        if inspect.isawaitable(result):
            result = await result
    except Exception:
        return []
    chunks: list[Chunk] = []
    for item in result or []:
        if isinstance(item, Chunk):
            chunks.append(item)
        else:
            chunks.append(Chunk.model_validate(item))
    return chunks


def rules_block(rag: Any) -> str:
    """Текст ``rules_system_block()``. Пустая строка, если метода нет или он упал."""
    fn = getattr(rag, "rules_system_block", None)
    if not callable(fn):
        return ""
    try:
        value = fn()
    except Exception:
        return ""
    if inspect.isawaitable(value):
        return ""
    return str(value or "")
