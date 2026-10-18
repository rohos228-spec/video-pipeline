"""Неблокирующее: resume зафиксирован, deleteMontageFrame только после confirm."""

from pathlib import Path


def test_resume_default_and_manual_frame_delete() -> None:
    api = Path("web/src/lib/api.ts").read_text(encoding="utf-8")
    assert 'params.set("mode", opts?.mode ?? "resume")' in api
    assert "повтор шага не делает wipe" in api
    board = Path("web/src/components/canvas/assemble-montage-board.tsx").read_text(encoding="utf-8")
    delete_at = board.find("api.deleteMontageFrame")
    assert delete_at > 0
    window = board.rfind("window.confirm", 0, delete_at)
    assert window > delete_at - 800
    assert board.count("api.deleteMontageFrame") == 1
