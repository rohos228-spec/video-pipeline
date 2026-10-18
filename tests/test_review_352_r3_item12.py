"""Ревью #352 п.12: on_progress четырёх нод коммитит под тем же локом, что и apply."""

import inspect

from app.orchestrator.steps.enrich_xlsx import _run_four_node_markup_pass


def test_markup_progress_holds_db_lock() -> None:
    src = inspect.getsource(_run_four_node_markup_pass)
    assert "async def _locked_progress" in src
    assert "async with _db_lock" in src
    assert "on_progress=_locked_progress" in src
