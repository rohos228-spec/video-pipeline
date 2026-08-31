"""Unit-тесты клиента Kling 2.6 (kie.ai) без сети."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.bots import kie_kling as kk


def test_truncate_kling_prompt() -> None:
    assert len(kk.truncate_kling_prompt("hi")) == 2
    long = "word " * 400
    out = kk.truncate_kling_prompt(long, max_chars=100)
    assert len(out) <= 100
    assert "word" in out


def test_map_kling_duration_and_aspect() -> None:
    assert kk.map_kling_duration(5) == "5"
    assert kk.map_kling_duration(7) == "5"
    assert kk.map_kling_duration(7.1) == "10"
    assert kk.map_kling_duration(None) == "5"
    assert kk.map_kling_aspect("9_16") == "9:16"
    assert kk.map_kling_aspect("Исходное") == "9:16"
    assert kk.map_kling_aspect("1:1") == "1:1"


def test_result_url_from_task() -> None:
    data = {
        "taskId": "t1",
        "resultJson": json.dumps({"resultUrls": ["https://cdn.example/v.mp4"]}),
    }
    assert kk._result_url_from_task(data) == "https://cdn.example/v.mp4"


@pytest.mark.asyncio
async def test_generate_video_i2v_flow(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(kk, "kie_api_configured", lambda: True)
    monkeypatch.setattr(kk, "kie_api_key", lambda: "test-key")
    monkeypatch.setattr(kk, "kie_api_base_url", lambda: "https://api.kie.ai")

    async def _fake_public(url, **_k):
        return "https://host.example/frame.jpg"

    monkeypatch.setattr(kk, "_frame_to_public_url", AsyncMock(return_value="https://host.example/frame.jpg"))

    created: dict = {}

    async def _create(body):
        created["body"] = body
        return "task_abc"

    async def _poll(task_id, *, timeout_s):
        assert task_id == "task_abc"
        return {
            "taskId": task_id,
            "state": "success",
            "resultJson": json.dumps({"resultUrls": ["https://cdn.example/out.mp4"]}),
        }

    async def _dl(url, out_path: Path):
        out_path.write_bytes(b"x" * 2048)
        return out_path

    monkeypatch.setattr(kk, "_create_task", _create)
    monkeypatch.setattr(kk, "_poll_task", _poll)
    monkeypatch.setattr(kk, "_download", _dl)

    out = tmp_path / "clip.mp4"
    frame = tmp_path / "f.png"
    frame.write_bytes(b"png")
    result = await kk.generate_video(
        "a" * 1200,
        out,
        start_frame=frame,
        duration=8,
        generate_audio=True,  # пайплайн всё равно шлёт False из retry
    )
    assert result.file_path == out
    assert created["body"]["model"] == kk.KLING_I2V_MODEL
    assert created["body"]["input"]["duration"] == "10"
    assert created["body"]["input"]["sound"] is True
    assert len(created["body"]["input"]["prompt"]) <= 1000
    assert created["body"]["input"]["image_urls"] == ["https://host.example/frame.jpg"]


@pytest.mark.asyncio
async def test_generate_video_t2v_without_frame(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(kk, "kie_api_configured", lambda: True)
    monkeypatch.setattr(kk, "_frame_to_public_url", AsyncMock(return_value=None))

    async def _create(body):
        assert body["model"] == kk.KLING_T2V_MODEL
        assert body["input"]["aspect_ratio"] == "9:16"
        assert body["input"]["sound"] is False
        return "task_t2v"

    async def _poll(task_id, *, timeout_s):
        return {
            "taskId": task_id,
            "state": "success",
            "resultJson": json.dumps({"resultUrls": ["https://cdn.example/t.mp4"]}),
        }

    async def _dl(url, out_path: Path):
        out_path.write_bytes(b"y" * 2048)
        return out_path

    monkeypatch.setattr(kk, "_create_task", _create)
    monkeypatch.setattr(kk, "_poll_task", _poll)
    monkeypatch.setattr(kk, "_download", _dl)

    await kk.generate_video("hello", tmp_path / "t.mp4", generate_audio=False)


@pytest.mark.asyncio
async def test_generate_video_fails_if_start_frame_host_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(kk, "kie_api_configured", lambda: True)
    monkeypatch.setattr(kk, "_frame_to_public_url", AsyncMock(return_value=None))
    frame = tmp_path / "f.png"
    frame.write_bytes(b"png")
    with pytest.raises(kk.KieKlingError) as ei:
        await kk.generate_video("hello", tmp_path / "t.mp4", start_frame=frame)
    assert ei.value.context.get("error_kind") == "frame_host"


@pytest.mark.asyncio
async def test_poll_retries_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kk, "kie_api_key", lambda: "k")
    monkeypatch.setattr(kk, "kie_api_base_url", lambda: "https://api.kie.ai")
    monkeypatch.setattr(kk, "_POLL_INTERVAL_S", 0.01)
    calls = {"n": 0}

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "code": 200,
                "data": {
                    "taskId": "t",
                    "state": "success",
                    "resultJson": json.dumps(
                        {"resultUrls": ["https://cdn.example/v.mp4"]}
                    ),
                },
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ReadError("boom")
            return FakeResp()

    monkeypatch.setattr(kk.httpx, "AsyncClient", lambda **k: FakeClient())
    data = await kk._poll_task("t", timeout_s=30)
    assert data["state"] == "success"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_poll_fail_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kk, "kie_api_key", lambda: "k")
    monkeypatch.setattr(kk, "kie_api_base_url", lambda: "https://api.kie.ai")

    class FakeResp:
        status_code = 200

        def json(self):
            return {
                "code": 200,
                "data": {
                    "taskId": "t",
                    "state": "fail",
                    "failCode": "501",
                    "failMsg": "generation failed",
                },
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(kk.httpx, "AsyncClient", lambda **k: FakeClient())
    with pytest.raises(kk.KieKlingError) as ei:
        await kk._poll_task("t", timeout_s=5)
    assert ei.value.context.get("provider_code") == 501


def test_kie_api_key_falls_back_to_gpt_on_vps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KIE_API_KEY", raising=False)
    monkeypatch.delenv("GPT_API_KEY", raising=False)
    monkeypatch.setattr(kk.settings, "kie_api_key", "")
    monkeypatch.setattr(kk.settings, "gpt_api_key", "kie-from-gpt")
    monkeypatch.setattr(kk.settings, "gpt_base_url", "https://gpt.video-pipeline.work")
    monkeypatch.setattr(kk.settings, "gpt_relay_token", "relay-secret")
    monkeypatch.setattr(
        "app.services.env_file.last_nonempty_dotenv_value",
        lambda *_a, **_k: "",
    )
    assert kk.kie_api_key() == "kie-from-gpt"
    assert kk.kie_api_key_source() == "GPT_API_KEY"


def test_kie_skips_relay_token_as_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KIE_API_KEY", "relay-secret")
    monkeypatch.setattr(kk.settings, "kie_api_key", "relay-secret")
    monkeypatch.setattr(kk.settings, "gpt_relay_token", "relay-secret")
    monkeypatch.setattr(kk.settings, "gpt_api_key", "real-kie-key")
    monkeypatch.delenv("GPT_API_KEY", raising=False)
    monkeypatch.setattr(
        "app.services.env_file.last_nonempty_dotenv_value",
        lambda *_a, **_k: "",
    )
    assert kk.kie_api_key() == "real-kie-key"


@pytest.mark.asyncio
async def test_kie_post_json_retries_next_key_on_model_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kk.reset_working_kie_key()
    monkeypatch.setattr(
        kk,
        "kie_key_candidates",
        lambda: [("KIE_API_KEY", "dead-key"), ("GPT_API_KEY", "live-key")],
    )
    monkeypatch.setattr(kk, "kie_uses_vps_relay", lambda: False)
    calls: list[str] = []

    class FakeResp:
        def __init__(self, payload: dict, status: int = 200) -> None:
            self._payload = payload
            self.status_code = status

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, **_k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def post(self, url, headers=None, json=None):
            auth = (headers or {}).get("Authorization", "")
            calls.append(auth)
            if auth.endswith("dead-key"):
                return FakeResp(
                    {"code": 401, "msg": "The API key is not authorized to use this model."}
                )
            return FakeResp({"code": 200, "data": {"taskId": "ok-1"}})

    monkeypatch.setattr(kk.httpx, "AsyncClient", FakeClient)
    _r, payload, source = await kk.kie_post_json(
        "https://api.kie.ai/api/v1/jobs/createTask", {"model": "x"}
    )
    assert source == "GPT_API_KEY"
    assert payload["data"]["taskId"] == "ok-1"
    assert calls == ["Bearer dead-key", "Bearer live-key"]
    assert kk.kie_api_key() == "live-key"
    kk.reset_working_kie_key()


def test_kie_create_goes_through_vps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(kk.settings, "kie_api_base_url", "https://api.kie.ai")
    monkeypatch.setattr(kk.settings, "gpt_relay_token", "relay-secret")
    monkeypatch.setattr(kk.settings, "gpt_base_url", "https://gpt.example.com")
    monkeypatch.setattr(kk, "kie_api_key", lambda: "kie-key")
    assert kk.kie_api_base_url() == "https://gpt.example.com"
    assert kk.kie_uses_vps_relay() is True
    headers = kk.kie_auth_headers()
    assert headers["Authorization"] == "Bearer kie-key"
    assert headers["X-VP-Relay-Token"] == "relay-secret"
