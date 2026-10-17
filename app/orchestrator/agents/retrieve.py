"""Общий retrieve для planner и critic.

Чужой RAG не импортируется. Синхронный мок тоже принимается.
"""

from __future__ import annotations

import inspect
from typing import Any

from app.orchestrator.contracts import Chunk


async def retrieve(rag: Any, query: str, corpora: list[str], top_k: int) -> list[Chunk]:
    result = rag.retrieve(query, corpora, top_k)
    if inspect.isawaitable(result):
        result = await result
    chunks: list[Chunk] = []
    for item in result or []:
        if isinstance(item, Chunk):
            chunks.append(item)
        else:
            chunks.append(Chunk.model_validate(item))
    return chunks
