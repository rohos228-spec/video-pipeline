"""Тесты pending sidecar / list_generation_files."""

from __future__ import annotations

from pathlib import Path

from app.services.generation_storage import (
    build_generation_path,
    list_generation_files,
    update_sidecar,
    write_sidecar,
)


def test_pending_appears_in_history_before_file(tmp_path: Path, monkeypatch) -> None:
    from app.services import generation_storage as gs

    monkeypatch.setattr(gs.settings, "data_dir", tmp_path)
    path = build_generation_path(media="image", model="gpt-image-2", ext=".png")
    write_sidecar(
        path,
        media="image",
        model="gpt-image-2",
        prompt="red apple",
        provider="outsee",
        status="queued",
        job_id="abc123",
        require_file=False,
    )
    assert not path.is_file()
    items = list_generation_files(kind="image")
    assert any(i["id"] == f"gen-{path.name}" for i in items)
    hit = next(i for i in items if i["id"] == f"gen-{path.name}")
    assert hit["status"] == "queued"
    assert hit["preview_url"] is None
    assert hit["label"] == "в очереди"

    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    update_sidecar(path, status="done")
    write_sidecar(
        path,
        media="image",
        model="gpt-image-2",
        prompt="red apple",
        provider="outsee",
        status="done",
        job_id="abc123",
    )
    items2 = list_generation_files(kind="image")
    hit2 = next(i for i in items2 if i["id"] == f"gen-{path.name}")
    assert hit2["status"] == "done"
    assert hit2["preview_url"]
