"""WinError 1314 — skip, любая другая ошибка symlink — падение."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from tests.actions.fs_helpers import _symlink_or_skip
from tests.actions.test_whitelist_no_exec import _symlinks_available


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


def test_symlink_probe_uses_gettempdir(tmp_path: Path, monkeypatch) -> None:
    """Проба не зависит от прав машины: symlink замокан."""
    probe_root = tmp_path / "probe-root"
    probe_root.mkdir()
    monkeypatch.setenv("TMPDIR", str(tmp_path / "not-this"))
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(probe_root))
    created: list[Path] = []
    real_mkdir = Path.mkdir

    def spy(self: Path, *args, **kwargs):
        created.append(self)
        return real_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", spy)

    def allow(src, dst, *args, **kwargs) -> None:
        return None

    monkeypatch.setattr(os, "symlink", allow)
    assert _symlinks_available() is True
    assert any(probe_root == path or probe_root in path.parents for path in created)
    assert not any(Path(os.environ["TMPDIR"]) in path.parents for path in created)

    def denied(src, dst, *args, **kwargs) -> None:
        err = OSError("A required privilege is not held by the client")
        err.winerror = 1314
        raise err

    monkeypatch.setattr(os, "symlink", denied)
    assert _symlinks_available() is False

    def other(src, dst, *args, **kwargs) -> None:
        err = OSError("access denied")
        err.winerror = 5
        raise err

    monkeypatch.setattr(os, "symlink", other)
    with pytest.raises(OSError, match="access denied"):
        _symlinks_available()
