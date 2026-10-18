"""Приватные хелперы atomic_write нельзя вызвать в обход allowlist."""

from __future__ import annotations

import pytest

from app.orchestrator.node_store import atomic_write as mod
from tests.actions.test_whitelist_no_exec import analyze_module_source


def test_private_atomic_helpers_are_denied() -> None:
    source = (
        "from pathlib import Path\n"
        "def save(fd, store):\n"
        "    _copy_nofollow(fd, 'a', fd, 'b')\n"
        "    _write_tmp(fd, 'x.json', b'z', 0o644)\n"
        "    _write_at(Path('data/node_overrides'), ['x.json'], 't', 0, 1)\n"
        "    _write_without_dir_fd(Path('templates/x.json'), 't', 0, 1)\n"
        "    store._open_dir('/etc')\n"
    )
    hits = analyze_module_source(source, "node.set_config")
    assert [hit.kind for hit in hits] == ["atomic_private"] * 5
    assert all(hit.allowed is False for hit in hits)
    with pytest.raises((TypeError, PermissionError)):
        mod._copy_nofollow(0, "a", 0, "b")
