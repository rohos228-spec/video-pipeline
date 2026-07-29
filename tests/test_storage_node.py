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
    p.data_dir.mkdir(parents=True, exist_ok=True)
    return p


def test_each_storage_node_has_own_folder(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    a, b = "n_storage_1", "n_storage_2"
    save_upload(p, a, "a.txt", b"aaa")
    save_upload(p, b, "b.txt", b"bbb")
    assert storage_dir(p, a) != storage_dir(p, b)
    files_a = list(storage_dir(p, a).glob("*.txt"))
    files_b = list(storage_dir(p, b).glob("*.txt"))
    assert len(files_a) == 1
    assert len(files_b) == 1
    assert files_a[0].read_bytes() == b"aaa"
    assert files_b[0].read_bytes() == b"bbb"
    assert len(list_stored_files(p, a)) == 1
    assert len(list_stored_files(p, b)) == 1


def test_sync_renames_with_node_and_time(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    scenes = p.data_dir / "scenes"
    scenes.mkdir(parents=True)
    (scenes / "f001.png").write_bytes(b"\x89PNG" + b"1" * 200)
    store = "n_storage_1"
    img = "n_images_3"
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
        "storage_nodes": {store: {"formats": ["any"]}},
    }
    res = sync_from_edges(p, store)
    assert res["okFileCount"] >= 1
    name = res["copied"][0]
    # n3_YYYYMMDD_HHMMSS_f001.png
    assert name.startswith("n3_")
    assert "f001.png" in name
    assert "_" in name
    entry = list_stored_files(p, store)[0]
    assert entry["fromNode"] == img
    assert entry["fromLabel"] == "n3"
    assert entry["originalName"] == "f001.png"
    assert entry["savedAt"]
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


def test_resolve_auto_syncs_all_incoming(tmp_path: Path, monkeypatch) -> None:
    """resolve с autoSync забирает файлы со всех входящих стрелок без ручного sync."""
    p = _project(tmp_path, monkeypatch)
    scenes = p.data_dir / "scenes"
    scenes.mkdir(parents=True)
    (scenes / "f001.png").write_bytes(b"\x89PNG" + b"1" * 200)
    weird = p.data_dir / "excel_gpt_uploads" / "n_gpt"
    weird.mkdir(parents=True)
    (weird / "report.bin").write_bytes(b"bin-data-123")
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": "n_images", "type": "images", "position": {"x": 0, "y": 0}},
                {"id": "n_gpt", "type": "excel_gpt", "position": {"x": 100, "y": 0}},
                {"id": "n_store", "type": "storage", "position": {"x": 200, "y": 0}},
            ],
            "edges": [
                {"id": "e1", "source": "n_images", "target": "n_store", "data": {"kind": "after"}},
                {"id": "e2", "source": "n_gpt", "target": "n_store", "data": {"kind": "after"}},
            ],
        },
        "gpt_operator_results": {
            "n_gpt": {
                "inputPaths": [],
                "outputPaths": [str(weird / "report.bin")],
                "replyPreview": "",
            }
        },
        "excel_gpt_nodes": {"n_gpt": {"role": "assist", "emitKinds": ["result"]}},
        "storage_nodes": {"n_store": {"formats": ["any"], "autoSync": True}},
    }
    res = resolve_storage(p, "n_store")
    names = {f["name"] for f in res["files"]}
    assert any("f001.png" in n for n in names)
    assert any("report.bin" in n for n in names)
    assert res["okFileCount"] >= 2


def test_clear_and_resolve(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    key = "n_storage_x"
    save_upload(p, key, "x.txt", b"hello")
    assert resolve_storage(p, key, auto_sync=False)["okFileCount"] == 1
    assert clear_storage(p, key) == 1
    assert resolve_storage(p, key, auto_sync=False)["okFileCount"] == 0


def test_build_storage_zip(tmp_path: Path, monkeypatch) -> None:
    from app.services.storage_node import build_storage_zip

    p = _project(tmp_path, monkeypatch)
    key = "n_store_zip"
    save_upload(p, key, "a.txt", b"aaa")
    save_upload(p, key, "b.txt", b"bbb")
    z = build_storage_zip(p, key)
    assert z.is_file() and z.suffix == ".zip"
    assert z.stat().st_size > 0
    # zip не должен попасть в список содержимого
    names = {f["name"] for f in list_stored_files(p, key)}
    assert z.name not in names
    assert len(names) == 2


def test_dedup_same_source_size(tmp_path: Path, monkeypatch) -> None:
    p = _project(tmp_path, monkeypatch)
    scenes = p.data_dir / "scenes"
    scenes.mkdir(parents=True)
    (scenes / "a.png").write_bytes(b"\x89PNG" + b"1" * 100)
    store = "n_store_dedup"
    src = "n_images_1"
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": src, "type": "images", "position": {"x": 0, "y": 0}},
                {"id": store, "type": "storage", "position": {"x": 200, "y": 0}},
            ],
            "edges": [
                {"id": "e1", "source": src, "target": store, "data": {"kind": "after"}},
            ],
        },
        "storage_nodes": {store: {"formats": ["any"]}},
    }
    r1 = sync_from_edges(p, store)
    r2 = sync_from_edges(p, store)
    assert len(r1["copied"]) == 1
    assert r2["copied"] == []
    assert resolve_storage(p, store, auto_sync=False)["okFileCount"] == 1


def test_dedup_same_xlsx_from_two_nodes(tmp_path: Path, monkeypatch) -> None:
    """Две ноды отдают один project.xlsx — в хранилище одна копия."""
    p = _project(tmp_path, monkeypatch)
    xlsx = p.data_dir / "project.xlsx"
    xlsx.write_bytes(b"PK" + b"excel-bytes-same" * 20)
    store = "n_store"
    a, b = "n_excel_gpt_1", "n_plan"
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": a, "type": "excel_gpt", "position": {"x": 0, "y": 0}, "data": {"slotIndex": 1}},
                {"id": b, "type": "plan", "position": {"x": 50, "y": 0}},
                {"id": store, "type": "storage", "position": {"x": 200, "y": 0}},
            ],
            "edges": [
                {"id": "e1", "source": a, "target": store, "data": {"kind": "after"}},
                {"id": "e2", "source": b, "target": store, "data": {"kind": "after"}},
            ],
        },
        "storage_nodes": {store: {"formats": ["any"]}},
    }
    res = sync_from_edges(p, store)
    assert len(res["copied"]) == 1
    assert resolve_storage(p, store, auto_sync=False)["okFileCount"] == 1
    res2 = sync_from_edges(p, store)
    assert res2["copied"] == []
    assert resolve_storage(p, store, auto_sync=False)["okFileCount"] == 1


def test_sync_downstream_storage_after_gpt(tmp_path: Path, monkeypatch) -> None:
    """После excel_gpt → storage подтягивается без открытия панели."""
    from app.services.gpt_operator import save_operator_result
    from app.services.storage_node import sync_downstream_storage_from_node

    p = _project(tmp_path, monkeypatch)
    xlsx = p.data_dir / "project.xlsx"
    xlsx.write_bytes(b"PK" + b"gpt-out" * 30)
    gpt, store = "n_excel_gpt_1", "n_storage_1"
    p.meta = {
        "canvas_graph": {
            "nodes": [
                {"id": gpt, "type": "excel_gpt", "position": {"x": 0, "y": 0}},
                {"id": store, "type": "storage", "position": {"x": 200, "y": 0}},
            ],
            "edges": [
                {"id": "e1", "source": gpt, "target": store, "data": {"kind": "after"}},
            ],
        },
        "excel_gpt_nodes": {
            gpt: {"role": "assist", "emitKinds": ["result", "reply_txt"], "transport": "api"}
        },
        "storage_nodes": {store: {"formats": ["any"], "autoSync": True}},
    }
    out = p.data_dir / "excel_gpt_uploads" / gpt
    out.mkdir(parents=True)
    reply = out / "gpt_reply.txt"
    reply.write_text("done", encoding="utf-8")
    save_operator_result(
        p,
        gpt,
        input_paths=[xlsx],
        output_paths=[xlsx, reply],
        reply_text="done",
    )
    synced = sync_downstream_storage_from_node(p, gpt)
    assert len(synced) == 1
    assert synced[0]["storageNode"] == store
    assert synced[0]["okFileCount"] >= 1
    assert resolve_storage(p, store, auto_sync=False)["okFileCount"] >= 1
