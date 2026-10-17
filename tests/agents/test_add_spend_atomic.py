"""Параллельные add_spend не теряют приращения."""

from __future__ import annotations

import threading

from app.orchestrator.agents.task_store import TaskStore
from app.orchestrator.contracts import TaskStatus


def test_add_spend_concurrent_threads_sum(token: str) -> None:
    del token
    store = TaskStore()
    task_id = store.insert("spend-race", "сумма", TaskStatus.queued.value)
    workers = 50
    barrier = threading.Barrier(workers)
    errors: list[BaseException] = []

    def _add() -> None:
        try:
            barrier.wait(timeout=5)
            store.add_spend(task_id, actions=1)
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_add) for _ in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert errors == []
    assert not any(thread.is_alive() for thread in threads)
    row = store.get(task_id)
    assert row is not None
    assert float(row["spend_actions"]) == float(workers)
