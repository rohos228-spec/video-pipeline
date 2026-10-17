"""Константы правил params ноды. Единственное место.

Правило «батчи параллельно, stagger_sec от 2 до 5 секунд» снято
пользователем и в main не переносится: ``stagger_sec`` и ``parallel``
здесь не проверяются.
"""

from __future__ import annotations

from typing import Any

from app.orchestrator.node_store._db import NodeStoreError

# Правило 2: reasoning_effort не опускается ниже high.
REQUIRED_REASONING_EFFORT = "high"
DEFAULT_TEXT_MODEL = "gpt-5.6-sol"


def default_params() -> dict[str, Any]:
    return {
        "model": DEFAULT_TEXT_MODEL,
        "reasoning_effort": REQUIRED_REASONING_EFFORT,
    }


def validate_reasoning_effort(params: dict[str, Any]) -> None:
    """Отказ, если в снимке ``reasoning_effort`` задан и это не ``high``."""
    if "reasoning_effort" not in params:
        return
    effort = params["reasoning_effort"]
    if effort != REQUIRED_REASONING_EFFORT:
        raise NodeStoreError(
            f"reasoning_effort ниже {REQUIRED_REASONING_EFFORT} запрещён"
        )
