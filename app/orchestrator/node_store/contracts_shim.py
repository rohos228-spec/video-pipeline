"""TODO: удалить после мёржа #346.

Пока ``app.orchestrator.contracts`` нет в main, здесь тонкий NodeDef
той же формы, что в PR #346 (коммит fab1b126). После мёржа импорт
берётся из contracts.py, и этот файл надо убрать.
"""

from __future__ import annotations

import importlib.util
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

# find_spec не выполняет модуль. ImportError внутри contracts (битая
# зависимость) не маскируется шимом: импорт ниже пробросит её наружу.
_CONTRACTS_SPEC = importlib.util.find_spec("app.orchestrator.contracts")

if _CONTRACTS_SPEC is not None:
    from app.orchestrator.contracts import NodeDef, NodeSource
else:

    class NodeSource(StrEnum):
        """Откуда resolve_node собрал определение (§3.6)."""

        git = "git"
        db = "db"

    class NodeDef(BaseModel):
        """Определение ноды для resolve_node и get_node.

        Поля совпадают с NodeDef из PR #346: активная версия в тип не
        копируется, колонки is_active нет.
        """

        node_key: str
        prompt_template: str = ""
        params: dict[str, Any] = Field(default_factory=dict)
        source: NodeSource = NodeSource.git
        version: int = 0
        db_version: int | None = None
        git_sha256: str | None = None
        stale_base: bool = False

__all__ = ["NodeDef", "NodeSource"]
