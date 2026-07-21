"""Preview URL с mtime — иначе UI не видит замену кадра."""

from __future__ import annotations

from pathlib import Path

from app.services.montage_board import _preview_url


def test_preview_url_includes_mtime_cache_bust(tmp_path: Path) -> None:
    p = tmp_path / "frame_018_a0f4b707.png"
    p.write_bytes(b"x" * 1000)
    url = _preview_url(p)
    assert url is not None
    assert "/api/files?path=" in url
    assert "&v=" in url
    mtime = int(p.stat().st_mtime)
    assert f"&v={mtime}" in url


def test_apply_progress_publishes_refresh_board() -> None:
    import inspect

    from app.services import montage_board_apply_job as job

    src = inspect.getsource(job.spawn_apply_job)
    assert "refresh_board" in src
