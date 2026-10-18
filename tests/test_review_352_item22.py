"""№22: GET доски не перевязывает роли; явный хелпер помечает повтор VO."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from app.services.montage_board import build_montage_board, sync_board_shot_roles


def test_build_montage_board_sync_shot_roles_defaults_off() -> None:
    default = inspect.signature(build_montage_board).parameters["sync_shot_roles"].default
    assert default is False


def test_sync_board_shot_roles_marks_later_duplicate_vo() -> None:
    first = SimpleNamespace(
        uuid="parent-uuid",
        number=1,
        sort_key=1.0,
        voiceover_text="Одна и та же фраза закадра.",
        attrs={},
    )
    second = SimpleNamespace(
        uuid="dup-uuid",
        number=2,
        sort_key=2.0,
        voiceover_text="Одна и та же фраза закадра.",
        attrs={},
    )
    n_dup, _n_relink = sync_board_shot_roles([first, second], None)
    assert n_dup >= 1
    assert ((second.attrs or {}).get("camera_subdivide") or {}).get("leftover") is True
    assert not ((first.attrs or {}).get("camera_subdivide") or {}).get("leftover")
