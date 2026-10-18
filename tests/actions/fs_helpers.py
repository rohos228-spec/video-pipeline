"""Общие помощники файловых тестов actions."""

from __future__ import annotations

from pathlib import Path

import pytest


def _symlink_or_skip(link: Path, target: Path, **kwargs) -> None:
    """Создать symlink. Нет привилегии (WinError 1314) — skip, не падение."""
    try:
        link.symlink_to(target, **kwargs)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("WinError 1314: нет привилегии на symlink")
        raise
