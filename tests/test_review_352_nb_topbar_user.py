"""Неблокирующее: мёртвые userOpen и UserPanel сняты с рельса."""

from pathlib import Path


def test_topbar_has_no_dead_user_panel() -> None:
    src = Path("web/src/components/shell/topbar.tsx").read_text(encoding="utf-8")
    assert "userOpen" not in src
    assert "UserPanel" not in src
    assert 'href = "/profile"' in src or 'href = "/profile"' in src.replace(" ", "")
    assert "/profile" in src
    assert "UserPrefsApplier" in src
