"""WaveSpeed Eleven V4: submit, poll, download через httpx.MockTransport."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from app.services.wavespeed_eleven_v4 import (
    DEFAULT_WAVESPEED_VOICE_ID,
    WaveSpeedElevenV4Error,
    split_text_for_eleven_v4,
    synthesize_speech,
)
from app.settings import settings

MP3 = b"ID3" + b"\x00" * 80


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_missing_key_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "wavespeed_api_key", "")
    monkeypatch.delenv("WAVESPEED_API_KEY", raising=False)
    with pytest.raises(WaveSpeedElevenV4Error, match="WAVESPEED_API_KEY"):
        await synthesize_speech("Привет, это озвучка.", tmp_path / "a.mp3")


@pytest.mark.asyncio
async def test_empty_text_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "wavespeed_api_key", "ws_test")
    with pytest.raises(WaveSpeedElevenV4Error, match="пуст"):
        await synthesize_speech("   ", tmp_path / "a.mp3")


@pytest.mark.asyncio
async def test_submit_poll_download(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "wavespeed_api_key", "ws_test_key")
    monkeypatch.setattr(settings, "wavespeed_eleven_v4_voice_id", "Alicia")
    monkeypatch.setattr(settings, "wavespeed_eleven_v4_stability", 0.5)
    monkeypatch.setattr(settings, "wavespeed_eleven_v4_similarity", 0.75)
    seen: dict[str, object] = {"polls": 0, "auth": []}
    out = tmp_path / "voice.mp3"

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"].append(request.headers.get("authorization"))
        url = str(request.url)
        if request.method == "POST" and url.endswith("/elevenlabs/eleven-v4"):
            body = json.loads(request.content.decode())
            seen["body"] = body
            assert request.headers["authorization"] == "Bearer ws_test_key"
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "message": "success",
                    "data": {"id": "pred_abc", "status": "created"},
                },
            )
        if url.endswith("/predictions/pred_abc/result"):
            seen["polls"] = int(seen["polls"]) + 1
            status = "processing" if seen["polls"] == 1 else "completed"
            outputs = [] if status != "completed" else ["https://cdn.example/out.mp3"]
            return httpx.Response(
                200,
                json={"code": 200, "message": "success", "data": {"id": "pred_abc", "status": status, "outputs": outputs}},
            )
        if url == "https://cdn.example/out.mp3":
            assert "authorization" not in {k.lower() for k in request.headers}
            return httpx.Response(200, content=MP3)
        return httpx.Response(404, text=f"unexpected {url}")

    async with _client(handler) as client:
        result = await synthesize_speech(
            "Привет, это тест озвучки WaveSpeed.",
            out,
            voice_id="Alicia",
            stability=0.5,
            similarity=0.75,
            timeout=5,
            poll_interval=0,
            client=client,
        )
    assert result == out
    assert out.read_bytes() == MP3
    assert seen["polls"] == 2
    body = seen["body"]
    assert body["voice_id"] == DEFAULT_WAVESPEED_VOICE_ID
    assert body["stability"] == 0.5
    assert body["similarity"] == 0.75
    assert "Привет" in body["text"]


@pytest.mark.asyncio
async def test_failed_status(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "wavespeed_api_key", "ws_test_key")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"code": 200, "message": "success", "data": {"id": "pred_fail"}})
        return httpx.Response(
            200,
            json={
                "code": 200,
                "message": "success",
                "data": {"id": "pred_fail", "status": "failed", "error": "quota exceeded"},
            },
        )

    async with _client(handler) as client:
        with pytest.raises(WaveSpeedElevenV4Error, match="failed"):
            await synthesize_speech(
                "Текст для ошибки провайдера.",
                tmp_path / "a.mp3",
                timeout=5,
                poll_interval=0,
                client=client,
            )


@pytest.mark.asyncio
async def test_http_401(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "wavespeed_api_key", "bad")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="unauthorized")

    async with _client(handler) as client:
        with pytest.raises(WaveSpeedElevenV4Error, match="401"):
            await synthesize_speech(
                "Текст при плохом ключе.",
                tmp_path / "a.mp3",
                timeout=5,
                poll_interval=0,
                client=client,
            )


@pytest.mark.asyncio
async def test_long_text_two_submits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "wavespeed_api_key", "ws_test_key")
    submits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST":
            submits["n"] += 1
            pid = f"pred{submits['n']}"
            return httpx.Response(200, json={"code": 200, "data": {"id": pid, "status": "created"}})
        if "/result" in url:
            pid = url.split("/predictions/")[1].split("/")[0]
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "id": pid,
                        "status": "completed",
                        "outputs": [f"https://cdn.example/{pid}.mp3"],
                    },
                },
            )
        return httpx.Response(200, content=MP3)

    async def fake_concat(parts: list[Path], out_path: Path) -> None:
        out_path.write_bytes(b"".join(p.read_bytes() for p in parts))

    monkeypatch.setattr(
        "app.services.wavespeed_eleven_v4._concat_mp3",
        fake_concat,
    )
    out = tmp_path / "long.mp3"
    async with _client(handler) as client:
        await synthesize_speech(
            "А" * 10001,
            out,
            timeout=5,
            poll_interval=0,
            client=client,
        )
    assert submits["n"] == 2
    assert out.stat().st_size > 32


def test_split_boundaries() -> None:
    assert split_text_for_eleven_v4("короткий") == ["короткий"]
    parts = split_text_for_eleven_v4("А" * 10001)
    assert [len(p) for p in parts] == [10000, 1]
    text = ("фраза. " * 2000).strip()
    chunks = split_text_for_eleven_v4(text)
    assert len(chunks) > 1
    assert all(len(c) <= 10000 for c in chunks)
    assert chunks[0].startswith("фраза.")


@pytest.mark.asyncio
async def test_stability_similarity_clamped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(settings, "wavespeed_api_key", "ws_test_key")
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            seen["body"] = json.loads(request.content.decode())
            return httpx.Response(200, json={"code": 200, "data": {"id": "pred_clamp"}})
        if str(request.url).endswith("/result"):
            return httpx.Response(
                200,
                json={
                    "code": 200,
                    "data": {
                        "id": "pred_clamp",
                        "status": "completed",
                        "outputs": ["https://cdn.example/clamp.mp3"],
                    },
                },
            )
        return httpx.Response(200, content=MP3)

    async with _client(handler) as client:
        await synthesize_speech(
            "Проверка границ stability.",
            tmp_path / "a.mp3",
            voice_id="  customVoice99  ",
            stability=2,
            similarity=-1,
            timeout=5,
            poll_interval=0,
            client=client,
        )
    assert seen["body"]["voice_id"] == "customVoice99"
    assert seen["body"]["stability"] == 1.0
    assert seen["body"]["similarity"] == 0.0
