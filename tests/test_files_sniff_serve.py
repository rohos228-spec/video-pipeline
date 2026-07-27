""" /api/files: .bin на диске → MIME/filename по magic bytes. """

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.settings import settings
from app.web.routers.artifacts import files_router

_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c63000100000500010d0a2db40000000049454e44ae426082"
)


def test_serve_bin_as_png(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    f = tmp_path / "pic.bin"
    f.write_bytes(_PNG)
    app = FastAPI()
    app.include_router(files_router, prefix="/api")
    c = TestClient(app)

    r = c.get("/api/files", params={"path": str(f), "download": 1})
    assert r.status_code == 200
    assert "image/png" in (r.headers.get("content-type") or "")
    assert "pic.png" in (r.headers.get("content-disposition") or "")

    r2 = c.get("/api/files", params={"path": str(f)})
    assert r2.status_code == 200
    assert "image/png" in (r2.headers.get("content-type") or "")
