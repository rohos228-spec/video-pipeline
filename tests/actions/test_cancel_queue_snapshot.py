"""Снимок очереди пишется до очистки cancel_queue_ops."""

from __future__ import annotations

from app.orchestrator.node_store.queue_book import QueueBook


def test_cancel_queue_ops_snapshots_queue_first() -> None:
    ops = [{"id": "a", "kind": "render"}, {"id": "b", "kind": "export"}]
    book = QueueBook(ops)
    book.cancel_queue_ops()
    assert book.ops == []
    assert book.snapshots
    assert book.snapshots[0]["before"] == ops
    assert book.snapshots[0]["before"] is not book.ops
