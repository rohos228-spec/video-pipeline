"""Ревью #352 п.23: отключённую ноду можно включить из настроек, тост не зовёт меню V."""

from pathlib import Path

STUDIO = Path("web/src/components/studio/node-studio.tsx").read_text(encoding="utf-8")
WORK = Path("web/src/components/studio/studio-workspace.tsx").read_text(encoding="utf-8")


def test_disabled_node_can_be_enabled_from_settings() -> None:
    assert "Включить ноду" in STUDIO
    assert "onEnableNode" in STUDIO
    assert "меню V" not in WORK
    assert "включите её в настройках ноды" in WORK
