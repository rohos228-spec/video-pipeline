"""Выбор провайдера озвучки: дефолт ElevenLabs, явный WaveSpeed."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.models import Project
from app.services.tts_provider import (
    PROVIDER_ELEVENLABS,
    PROVIDER_WAVESPEED_ELEVEN_V4,
    TtsError,
    elevenlabs_tts_enabled,
    resolve_tts_provider,
    synthesize_project_audio,
)
from app.services.wavespeed_eleven_v4 import WaveSpeedElevenV4Error
from app.settings import settings


def _project(**audio: object) -> Project:
    project = Project(slug="tts-proj")
    project.id = 7
    if audio:
        project.meta = {"node_step_params": {"audio": dict(audio)}}
    else:
        project.meta = {}
    return project


def test_default_provider_is_elevenlabs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "audio_tts_provider", "elevenlabs")
    monkeypatch.delenv("AUDIO_TTS_PROVIDER", raising=False)
    assert resolve_tts_provider(_project()) == PROVIDER_ELEVENLABS


def test_env_and_node_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "audio_tts_provider", "wavespeed_eleven_v4")
    assert resolve_tts_provider(_project()) == PROVIDER_WAVESPEED_ELEVEN_V4
    assert (
        resolve_tts_provider(_project(tts_provider="elevenlabs"))
        == PROVIDER_ELEVENLABS
    )
    assert (
        resolve_tts_provider(_project(tts_provider="wavespeed_eleven_v4"))
        == PROVIDER_WAVESPEED_ELEVEN_V4
    )


def test_unknown_provider_raises() -> None:
    with pytest.raises(TtsError, match="Неизвестный провайдер"):
        resolve_tts_provider(_project(tts_provider="suno"))


def test_elevenlabs_gate_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "audio_use_elevenlabs_fallback", False)
    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    monkeypatch.delenv("AUDIO_USE_ELEVENLABS_FALLBACK", raising=False)
    assert elevenlabs_tts_enabled() is False
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk_live")
    assert elevenlabs_tts_enabled() is True


@pytest.mark.asyncio
async def test_wavespeed_without_key_is_explicit_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "wavespeed_api_key", "")
    monkeypatch.delenv("WAVESPEED_API_KEY", raising=False)
    project = _project(tts_provider="wavespeed_eleven_v4")
    with pytest.raises(WaveSpeedElevenV4Error, match="WAVESPEED_API_KEY"):
        await synthesize_project_audio(
            "Текст озвучки без ключа WaveSpeed.",
            tmp_path / "a.mp3",
            project=project,
            timeout=5,
        )


@pytest.mark.asyncio
async def test_selected_wavespeed_calls_wavespeed_not_elevenlabs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "wavespeed_api_key", "ws_test")
    monkeypatch.setattr(settings, "wavespeed_eleven_v4_voice_id", "Alicia")
    called: dict[str, object] = {}

    async def fake_ws(text, out_path, **kwargs):
        called["text"] = text
        called["voice_id"] = kwargs.get("voice_id")
        called["stability"] = kwargs.get("stability")
        called["similarity"] = kwargs.get("similarity")
        out_path.write_bytes(b"ID3" + b"\x00" * 40)
        return out_path

    eleven = AsyncMock(side_effect=AssertionError("elevenlabs must not be called"))
    monkeypatch.setattr("app.services.tts_provider.synthesize_wavespeed", fake_ws)
    monkeypatch.setattr("app.services.tts_provider.synthesize_elevenlabs", eleven)
    project = _project(tts_provider="wavespeed_eleven_v4", wavespeed_voice_id="pNInz6obpgDQGcFmaJgB")
    provider = await synthesize_project_audio(
        "Озвучка через WaveSpeed.",
        tmp_path / "a.mp3",
        project=project,
        timeout=5,
    )
    assert provider == PROVIDER_WAVESPEED_ELEVEN_V4
    assert called["voice_id"] == "pNInz6obpgDQGcFmaJgB"
    assert called["stability"] == 0.5
    assert called["similarity"] == 0.75
    eleven.assert_not_called()


@pytest.mark.asyncio
async def test_default_path_still_calls_elevenlabs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(settings, "audio_tts_provider", "elevenlabs")
    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk_test")
    monkeypatch.delenv("AUDIO_TTS_PROVIDER", raising=False)

    async def fake_el(text, out_path, **kwargs):
        assert kwargs["voice_id"]
        out_path.write_bytes(b"ID3" + b"\x00" * 40)
        return out_path

    wavespeed = AsyncMock(side_effect=AssertionError("wavespeed must not be called"))
    monkeypatch.setattr("app.services.tts_provider.synthesize_elevenlabs", fake_el)
    monkeypatch.setattr("app.services.tts_provider.synthesize_wavespeed", wavespeed)
    provider = await synthesize_project_audio(
        "Обычный путь ElevenLabs.",
        tmp_path / "a.mp3",
        project=_project(),
        timeout=5,
    )
    assert provider == PROVIDER_ELEVENLABS
    wavespeed.assert_not_called()
