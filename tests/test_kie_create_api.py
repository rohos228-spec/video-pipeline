"""REST kie-create: каталог, estimate, валидация, generate через мок kie_http."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.web.routers import kie_create


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(kie_create.router, prefix="/api")
    return app


def test_catalog_endpoint_lists_models() -> None:
    c = TestClient(_app())
    r = c.get("/api/kie-create/catalog")
    assert r.status_code == 200
    data = r.json()
    assert data["credit_usd"] == 0.005
    assert len(data["models"]) >= 50
    veo = next(m for m in data["models"] if m["id"] == "veo-3-1")
    assert any(f["name"] == "resolution" for f in veo["fields"])
    assert veo["pricing"]["rules"]
    # Старый UI глушит кнопку, если configured=false — даже при живом промпте.
    assert data["configured"] is True
    assert data["configured"] is True


def test_estimate_endpoint_dynamic() -> None:
    c = TestClient(_app())
    r = c.post(
        "/api/kie-create/estimate",
        json={"model_id": "seedance-2-5", "values": {"resolution": "1080p", "duration": 10}},
    )
    assert r.status_code == 200
    assert r.json()["credits"] == 114 * 10
    r2 = c.post(
        "/api/kie-create/estimate",
        json={"model_id": "seedance-2-5", "values": {"resolution": "480p", "duration": 5}},
    )
    assert r2.json()["credits"] == 28 * 5
    assert r.json()["usd"] > r2.json()["usd"]


def test_generate_validation_422() -> None:
    c = TestClient(_app())
    r = c.post(
        "/api/kie-create/generate",
        json={"model_id": "seedance-2-5", "values": {"resolution": "999p"}},
    )
    assert r.status_code == 422


def test_generate_unknown_model_404() -> None:
    c = TestClient(_app())
    r = c.post("/api/kie-create/generate", json={"model_id": "nope", "values": {}})
    assert r.status_code == 404


def test_generate_enqueues_job(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import create_jobs as cj
    from app.services import generation_storage as gs

    monkeypatch.setattr(gs.settings, "data_dir", tmp_path)
    monkeypatch.setattr(kie_create.kie_http, "kie_configured", lambda: True)
    cj._JOBS.clear()
    cj._SEMS.clear()
    cj._SEM_SIZES.clear()
    cj._RECENT_FP.clear()

    async def fake_run(spec, payload, out_path: Path):
        assert payload["model"] == "bytedance/seedance-2-5"
        assert payload["input"]["resolution"] == "720p"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"\x00" * 64)
        from app.bots.outsee import GenerationResult

        return GenerationResult(file_path=out_path, gen_id="task-1", raw_url=None)

    monkeypatch.setattr(kie_create.kie_http, "run_generation", fake_run)

    c = TestClient(_app())
    r = c.post(
        "/api/kie-create/generate",
        json={
            "model_id": "seedance-2-5",
            "values": {"prompt": "тестовый ролик", "resolution": "720p", "duration": 5},
        },
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["job"]["status"] in ("queued", "processing", "done")
    assert data["estimate"]["credits"] == 63 * 5
    assert data["estimate"]["usd"] == round(63 * 5 * 0.005, 4)
    job_id = data["job"]["job_id"]

    # фоновая задача добегает моком → файл появляется
    for _ in range(50):
        jr = c.get(f"/api/kie-create/jobs/{job_id}")
        if jr.json()["status"] in ("done", "failed"):
            break
        import time

        time.sleep(0.1)
    jr = c.get(f"/api/kie-create/jobs/{job_id}").json()
    assert jr["status"] == "done", jr.get("error")
    assert Path(jr["path"]).is_file()


def test_upload_requires_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kie_create.kie_http, "kie_configured", lambda: False)
    c = TestClient(_app())
    r = c.post(
        "/api/kie-create/upload",
        files={"file": ("a.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 32, "image/png")},
    )
    assert r.status_code == 503


def test_credits_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_none():
        return None

    monkeypatch.setattr(kie_create.kie_http, "get_credits", fake_none)
    monkeypatch.setattr(kie_create.kie_http, "kie_configured", lambda: False)
    c = TestClient(_app())
    r = c.get("/api/kie-create/credits")
    assert r.status_code == 200
    data = r.json()
    assert data["configured"] is False
    assert data["credits"] is None and data["usd"] is None
    assert "key_fingerprint" in data and "key_source" in data


@pytest.mark.asyncio
async def test_kie_http_extract_urls_nested() -> None:
    from app.bots.kie_http import _extract_urls

    data = {
        "response": {
            "sunoData": [
                {"audioUrl": "https://x/a.mp3", "imageUrl": "https://x/a.png"},
                {"audioUrl": "https://x/b.mp3"},
            ]
        }
    }
    urls = _extract_urls(data)
    assert "https://x/a.mp3" in urls and "https://x/b.mp3" in urls


@pytest.mark.asyncio
async def test_kie_http_prefers_longest_suno_mp3_not_cover() -> None:
    from app.bots.kie_http import _result_media_urls

    data = {
        "status": "SUCCESS",
        "response": {
            "sunoData": [
                {
                    "audioUrl": "https://x/short.mp3",
                    "imageUrl": "https://x/cover.jpeg",
                    "streamAudioUrl": "https://musicfile.kie.ai/stream-no-ext",
                    "duration": 2.0,
                },
                {
                    "audioUrl": "https://x/long.mp3",
                    "imageUrl": "https://x/cover2.jpeg",
                    "duration": 8.04,
                },
            ]
        },
    }
    urls = _result_media_urls(data)
    assert urls[0] == "https://x/long.mp3"
    assert all(_u.endswith(".mp3") for _u in urls)
    assert "https://x/cover.jpeg" not in urls


def test_kie_http_task_state_mapping() -> None:
    from app.bots.kie_http import _task_state

    assert _task_state("jobs", {"state": "success"}) == "success"
    assert _task_state("jobs", {"state": "fail"}) == "fail"
    assert _task_state("jobs", {"state": "generating"}) == "pending"
    assert _task_state("veo", {"successFlag": 1}) == "success"
    assert _task_state("veo", {"successFlag": 0}) == "pending"
    assert _task_state("suno", {"status": "SUCCESS"}) == "success"
    assert _task_state("suno", {"status": "PENDING"}) == "pending"
    assert _task_state("suno", {"status": "FAILED"}) == "fail"
