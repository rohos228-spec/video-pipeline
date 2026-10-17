"""Повторное ревью #352 №9: якоря покрытия не перенумеровывают кадры."""

from __future__ import annotations

import inspect

from app.services.montage_coverage_ops import apply_coverage_anchors


def test_apply_coverage_anchors_grows_without_renumber() -> None:
    src = inspect.getsource(apply_coverage_anchors)
    assert "_grow_cell_shots(" in src
    assert "renumber=False" in src
