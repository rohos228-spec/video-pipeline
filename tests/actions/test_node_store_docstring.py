"""Docstring пакета node_store называет модели, которые в нём лежат."""

from __future__ import annotations

import app.orchestrator.node_store as node_store


def test_node_store_docstring_names_its_models() -> None:
    doc = node_store.__doc__ or ""
    assert "meta_book" in doc
    assert "prompt_history" in doc
    assert "queue_book" in doc
