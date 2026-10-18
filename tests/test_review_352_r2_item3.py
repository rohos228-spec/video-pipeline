"""Повторное ревью #352 №3: ручной реф не выходит за каталог проекта."""

from __future__ import annotations

from pathlib import Path

from app.services.montage_frame_refs import _ref_path


def test_ref_path_rejects_parent_traversal_and_keeps_project_file(tmp_path: Path) -> None:
    data = tmp_path / "proj"
    data.mkdir()
    inside = data / "refs" / "hero.png"
    inside.parent.mkdir()
    inside.write_bytes(b"png")
    secret = tmp_path / "secrets.png"
    secret.write_bytes(b"no")

    kept = _ref_path(data, {"file": "refs/hero.png"})
    assert kept == inside.resolve()

    assert _ref_path(data, {"file": "../../secrets.png"}) is None
    assert _ref_path(data, {"file": str(secret)}) is None
