"""WinError 1314 — skip, любая другая ошибка symlink — падение."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.actions.fs_helpers import _symlink_or_skip


def test_symlink_or_skip_skips_only_winerror_1314(
    tmp_path: Path, monkeypatch
) -> None:
    def denied(self: Path, target, **kwargs) -> None:
        err = OSError("A required privilege is not held by the client")
        err.winerror = 1314
        raise err

    monkeypatch.setattr(Path, "symlink_to", denied)
    with pytest.raises(pytest.skip.Exception):
        _symlink_or_skip(tmp_path / "link", tmp_path / "target")

    def other(self: Path, target, **kwargs) -> None:
        err = OSError("access denied")
        err.winerror = 5
        raise err

    monkeypatch.setattr(Path, "symlink_to", other)
    with pytest.raises(OSError, match="access denied"):
        _symlink_or_skip(tmp_path / "link", tmp_path / "target")
