"""Кэш списка Create-генераций — не перечитывать json на каждый poll."""

from __future__ import annotations

from pathlib import Path

from app.services.generation_storage import (
    invalidate_generation_list_cache,
    list_generation_files,
    write_sidecar,
)


def test_list_generation_files_uses_cache(tmp_path: Path, monkeypatch) -> None:
    from app.services import generation_storage as gs

    monkeypatch.setattr(gs.settings, "data_dir", tmp_path)
    invalidate_generation_list_cache()

    path = tmp_path / "generations" / "image" / "m" / "20260727" / "a.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    write_sidecar(
        path,
        media="image",
        model="m",
        prompt="x",
        provider="outsee",
        status="done",
    )

    a = list_generation_files(kind="image", import_legacy=False)
    b = list_generation_files(kind="image", import_legacy=False)
    assert a == b
    assert len(a) == 1

    # без invalidate тот же кэш; после новой записи — новый список
    path2 = path.with_name("b.png")
    path2.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    write_sidecar(
        path2,
        media="image",
        model="m",
        prompt="y",
        provider="outsee",
        status="done",
    )
    c = list_generation_files(kind="image", import_legacy=False)
    assert len(c) == 2
