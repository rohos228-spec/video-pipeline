"""Приватные хелперы atomic_write нельзя вызвать в обход allowlist."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.orchestrator.node_store import atomic_write as mod
from tests.actions.test_whitelist_no_exec import analyze_module_source


def test_private_atomic_helpers_are_denied() -> None:
    source = (
        "from app.orchestrator.node_store.atomic_write import _INTERNAL, _write_tmp as wt\n"
        "import app.orchestrator.node_store.atomic_write as store\n"
        "from pathlib import Path\n"
        "def save(fd):\n"
        "    wt(fd, 'x.json', b'z', 0o644, _from=_INTERNAL)\n"
        "    f = store._copy_nofollow\n"
        "    getattr(store, '_copy_nofollow')\n"
        "    _descend(Path('data/node_overrides'), [])\n"
        "    _open_old(fd)\n"
        "    _copy_nofollow(fd, 'a', fd, 'b')\n"
        "    _write_tmp(fd, 'x.json', b'z', 0o644)\n"
        "    _write_at(Path('data/node_overrides'), ['x.json'], 't', 0, 1)\n"
        "    _write_without_dir_fd(Path('templates/x.json'), 't', 0, 1)\n"
        "    store._open_dir('/etc')\n"
    )
    hits = analyze_module_source(source, "node.set_config")
    assert [hit.kind for hit in hits] == ["atomic_private"] * 13
    assert all(hit.allowed is False for hit in hits)
    with pytest.raises(PermissionError):
        mod._copy_nofollow(0, "a", 0, "b")
    alias = mod._write_tmp
    with pytest.raises(PermissionError):
        alias(0, "x.json", b"z", 0o644)
    ref = mod._descend
    assert callable(ref)
    with pytest.raises(PermissionError):
        ref(Path("."), [])
    with pytest.raises(ImportError):
        exec(
            "from app.orchestrator.node_store.atomic_write import _INTERNAL",
            {},
        )
