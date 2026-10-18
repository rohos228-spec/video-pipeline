"""Неблокирующее: полоса хранилища считается от объёма диска, не от free."""

from pathlib import Path


def test_storage_bar_uses_disk_total() -> None:
    src = Path("web/src/components/panels/left-top/storage-panel.tsx").read_text(encoding="utf-8")
    assert "usage?.disk.total" in src
    assert "limit_bytes в API — свободное место" in src
    assert "const limit = usage ? usage.total_bytes + usage.limit_bytes : 0;" not in src
