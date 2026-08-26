"""Unit-тесты прямого HTTP REST API клиента ElevenLabs."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.models import Project
from app.services.elevenlabs_api import (
    ElevenLabsApiError,
    elevenlabs_api_configured,
    synthesize_speech,
)
from app.services.elevenlabs_voices import (
    DEFAULT_ELEVENLABS_VOICE_ID,
    ELEVENLABS_VOICES,
    resolve_elevenlabs_voice_id,
)


def test_elevenlabs_voices_catalog() -> None:
    """Каталог голосов содержит проверенные студийные пресеты."""
    assert len(ELEVENLABS_VOICES) >= 5
    ids = [v["id"] for v in ELEVENLABS_VOICES]
    assert DEFAULT_ELEVENLABS_VOICE_ID in ids
    assert "pNInz6obpgDQGcFmaJgB" in ids  # Adam


def test_resolve_elevenlabs_voice_id_default() -> None:
    """Если в meta ничего нет, возвращается default (Адам)."""
    p = Project(slug="test-proj")
    p.meta = {}
    assert resolve_elevenlabs_voice_id(p) == DEFAULT_ELEVENLABS_VOICE_ID


def test_resolve_elevenlabs_voice_id_custom() -> None:
    """Если в meta выбран валидный голос, возвращается его ID."""
    p = Project(slug="test-proj")
    p.meta = {
        "node_step_params": {
            "audio": {
                "elevenlabs_voice_id": "JBFqnCBsd6RMkjVDRZzb",  # George
            }
        }
    }
    assert resolve_elevenlabs_voice_id(p) == "JBFqnCBsd6RMkjVDRZzb"


@pytest.mark.asyncio
async def test_synthesize_speech_empty_text_raises() -> None:
    """Пустой текст вызывает ошибку."""
    with pytest.raises(ElevenLabsApiError, match="Текст для озвучки пуст"):
        await synthesize_speech("", Path("dummy.mp3"))


@pytest.mark.asyncio
async def test_synthesize_speech_missing_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """При отсутствии ключа выбрасывается ElevenLabsApiError."""
    from app.settings import settings

    monkeypatch.setattr(settings, "elevenlabs_api_key", "")
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)

    with pytest.raises(ElevenLabsApiError, match="Не задан ELEVENLABS_API_KEY"):
        await synthesize_speech("Тестовый текст", Path("dummy.mp3"))


@pytest.mark.asyncio
async def test_synthesize_speech_mock_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Успешный вызов HTTP API сохраняет MP3 на диск."""
    from app.settings import settings

    monkeypatch.setattr(settings, "elevenlabs_api_key", "sk_test_key_123")
    out_file = tmp_path / "test_out.mp3"
    fake_mp3_payload = b"ID3" + b"\x00" * 2000  # valid-ish mp3 header + bytes > 1000

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_mp3_payload

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        res = await synthesize_speech(
            "Привет, мир! Тестируем озвучку.",
            out_file,
            voice_id="pNInz6obpgDQGcFmaJgB",
        )
        assert res == out_file
        assert out_file.exists()
        assert out_file.read_bytes() == fake_mp3_payload

        # Проверяем, что запрос ушёл на правильный URL с заголовками
        call_url = mock_post.call_args[0][0]
        assert "https://api.elevenlabs.io/v1/text-to-speech/pNInz6obpgDQGcFmaJgB" == call_url
        headers = mock_post.call_args[1]["headers"]
        assert headers["xi-api-key"] == "sk_test_key_123"


def test_normalize_text_for_tts() -> None:
    """Числа и даты преобразуются в слова для безупречной озвучки."""
    from app.services.elevenlabs_api import normalize_text_for_tts

    assert normalize_text_for_tts("В 2050 году мир изменится") == "В две тысячи пятидесятом году мир изменится"
    assert normalize_text_for_tts("Жизнь в 2050") == "Жизнь в две тысячи пятидесятом"
    assert normalize_text_for_tts("Успех 100%") == "Успех сто процентов"
    assert normalize_text_for_tts("Скидка 50%") == "Скидка пятьдесят процентов"
    assert normalize_text_for_tts("") == ""
