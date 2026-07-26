"""Нода «Хранилище»: изоляция по node_key + sync со стрелок."""

from __future__ import annotations

from pathlib import Path

from app.models import Project, ProjectStatus
from app.services.gpt_operator import files_from_source_node
from app.services.storage_node import (
    clear_storage,
    list_stored_files,
    patch_config,
    resolve_storage,
    save_upload,
    storage_dir,
    sync_from_edges,
)


def _project(tmp_path: Path, monkeypatch) -> Project:
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    p = Project(
        slug="store",
        topic="t",
        status=ProjectStatus.new,
        hero_mode="no_hero",
        meta={},
    )
    p.data_dir.mkdir(parents=True)
    return p


def test_each_storage_node_has_own_folder(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    a, b = "n_storage_1", "n_storage_2"
    save_upload(p, a, "a.txt", b"aaa")
    save_upload(p, b, "b.txt", b"bbb")
    assert storage_dir(p, a) != storage_dir(p, b)
    assert (storage_dir(p, a) / "a.txt").read_bytes() == b"aaa"
    assert (storage_dir(p, b) / "b.txt").read_bytes() == b"bbb"
    assert len(list_stored_files(p, a)) == 1
    assert len(list_stored_files(p, b)) == 1


def test_sync_from_edge_copies_images(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    scenes = p.data_dir / "scenes"
    scenes.mkdir(parents=True)
    (scenes / "f001.png").write_bytes(b"\x89PNG" + b"1" * 200)
    store = "n_storage_1"
    img = "n_images"
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": img, "type": "images", "position": {"x": 0, "y": 0}},
                {"id": store, "type": "storage", "position": {"x": 200, "y": 0}},
            ],
            "edges": [
                {"id": "e1", "source": img, "target": store, "data": {"kind": "after"}},
            ],
        },
        "storage_nodes": {store: {"formats": ["image"]}},
    }
    res = sync_from_edges(p, store)
    assert res["okFileCount"] >= 1
    assert any("f001.png" in name for name in res["copied"])
    paths = files_from_source_node(p, store)
    assert paths
    assert all(p0.is_file() for p0 in paths)


def test_format_filter_skips_video(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    vids = p.data_dir / "videos"
    vids.mkdir(parents=True)
    (vids / "clip.mp4").write_bytes(b"0" * 300)
    store = "n_storage_1"
    src = "n_videos"
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": src, "type": "videos", "position": {"x": 0, "y": 0}},
                {"id": store, "type": "storage", "position": {"x": 200, "y": 0}},
            ],
            "edges": [
                {"id": "e1", "source": src, "target": store, "data": {"kind": "after"}},
            ],
        },
    }
    patch_config(p, store, {"formats": ["image"]})
    res = sync_from_edges(p, store)
    assert res["okFileCount"] == 0
    assert res["copied"] == []


def test_clear_and_resolve(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    key = "n_storage_x"
    save_upload(p, key, "x.txt", b"hello")
    assert resolve_storage(p, key)["okFileCount"] == 1
    assert clear_storage(p, key) == 1
    assert resolve_storage(p, key)["okFileCount"] == 0
