"""Общий потолок одновременных LLM-пачек. Не шлюз оплаты."""

from __future__ import annotations

import os

_DEFAULT_PACK_LIMIT = 8


def llm_pack_limit() -> int:
    """Env LLM_PACK_PARALLEL, иначе 8. Значение меньше 1 поднимается до 1."""
    raw = os.environ.get("LLM_PACK_PARALLEL", "").strip()
    if not raw:
        return _DEFAULT_PACK_LIMIT
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_PACK_LIMIT
    return max(1, value)
