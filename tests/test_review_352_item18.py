"""№18: параллельные батчи 4 нод не пишут в одну сессию без замка."""

from __future__ import annotations

import inspect

from app.orchestrator.steps.enrich_xlsx import _run_four_node_markup_pass


def test_four_node_apply_batch_holds_db_lock() -> None:
    src = inspect.getsource(_run_four_node_markup_pass)
    assert "_db_lock = asyncio.Lock()" in src
    assert "async with _db_lock:" in src
    assert src.index("_db_lock = asyncio.Lock()") < src.index("async def _apply_batch")
    assert src.index("async with _db_lock:") < src.index("await _db_apply.apply_ops")
