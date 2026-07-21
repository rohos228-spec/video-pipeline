"""Apply монтажа: Generate как нода — без recover_before."""

from __future__ import annotations

import inspect

from app.services import montage_board_apply as apply_mod


def test_apply_does_not_recover_before_generate() -> None:
    src = inspect.getsource(apply_mod.apply_montage_board)
    assert "recover_before_regen_ops" not in src
    assert "recover_before_regen" not in src or "НЕ recover_before" in src
