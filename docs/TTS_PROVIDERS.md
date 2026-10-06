# Озвучка: ElevenLabs и WaveSpeed Eleven V4

Пайплайн (`generate_audio` → `synthesize_per_frame_audio`) берёт один провайдер
на весь закадр. Готовый mp3 в `audio/` по-прежнему идёт только в ASR, без TTS.

Выбор, по приоритету:

1. Нода «Озвучка» в Studio: `meta.node_step_params.audio.tts_provider`
   (`elevenlabs` или `wavespeed_eleven_v4`). Пустое значение — следующий пункт.
2. `.env`: `AUDIO_TTS_PROVIDER` (дефолт `elevenlabs`).
3. Иначе прямой ElevenLabs.

Ключи только на сервере. В `web/` их нет.

## ElevenLabs (дефолт)

- Клиент: `app/services/elevenlabs_api.py`
- `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}`
- Ключ: `ELEVENLABS_API_KEY` (`xi-api-key`)
- Модель: `ELEVENLABS_MODEL_ID`, дефолт `eleven_multilingual_v2`
- Голос: каталог в ноде или Адам
- Если нет файла озвучки, шаг запускает TTS когда задан ключ или
  `AUDIO_USE_ELEVENLABS_FALLBACK=1`. Иначе просит положить mp3.
- Playwright-бот `app/bots/elevenlabs.py` остаётся запасным путём, если в вызов
  передан браузер и API-ключ пуст.

## WaveSpeed Eleven V4

- Клиент: `app/services/wavespeed_eleven_v4.py`
- `POST https://api.wavespeed.ai/api/v3/elevenlabs/eleven-v4`
- Опрос: `GET https://api.wavespeed.ai/api/v3/predictions/{id}/result` (~2 с)
  до `completed` / `failed` / `cancelled` / `timeout` / `deleted`
- Ключ: `WAVESPEED_API_KEY` (`Authorization: Bearer`)
- Тело: `text`, `voice_id` (пресет, дефолт Alicia, или ID ElevenLabs),
  `stability` (0..1, дефолт 0.5), `similarity` (0..1, дефолт 0.75)
- MP3 берётся из `data.outputs` (URL) и сохраняется локально
- Текст длиннее 10000 символов режется и склеивается ffmpeg
- Цена в документации WaveSpeed: **$0.08 / 1000 символов**
- Если провайдер выбран, а ключа нет — шаг падает с понятной ошибкой и
  **не** откатывается на ElevenLabs

```env
AUDIO_TTS_PROVIDER=wavespeed_eleven_v4
WAVESPEED_API_KEY=
WAVESPEED_ELEVEN_V4_VOICE_ID=Alicia
WAVESPEED_ELEVEN_V4_STABILITY=0.5
WAVESPEED_ELEVEN_V4_SIMILARITY=0.75
```

Общий вход: `app/services/tts_provider.py` (`synthesize_project_audio`).
