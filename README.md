# video-pipeline

Автоматический конвейер генерации коротких роликов (60–75 сек, 9:16) с оркестрацией, Telegram-ботом для HITL-подтверждений и уведомлений, и интеграциями с outsee.io (Nano Banana 2 + Veo 3.1 Fast Relax), ChatGPT web, 11Labs, Whisper, FFmpeg и MoreLogin.

## Стек

- Python 3.11
- PostgreSQL (состояние)
- SQLAlchemy 2 + Alembic (ORM + миграции)
- Prefect 2 или собственный лёгкий workflow-engine (решение на этапе MVP)
- aiogram 3 (Telegram-бот) + FastAPI (внутренний API / webhook)
- Playwright (async, через CDP `localhost:29229` к существующему Chrome)
- faster-whisper (локальный Whisper)
- ffmpeg-python (обёртка над FFmpeg)
- pydantic-settings (конфиги), loguru (логи)
- Docker Compose (портабельность)

## Ключевые решения

- **Короткий формат:** 60–75 сек, вертикаль 9:16, кадры по 2–4 сек, 15–30 кадров на ролик, 1000–1300 знаков текста.
- **1 слой:** все кадры одиночные, без «сборных сцен».
- **БД — источник правды.** Excel генерится экспортом по запросу из Telegram-бота.
- **HITL-гейты** по умолчанию: концепт/план, сценарий, референс ГГ (если нужен), все сцены перед анимацией, все видео перед сборкой, финальный ролик перед публикацией.
- **Площадки:** TikTok, YouTube Shorts, Instagram Reels, VK Клипы, Likee (через MoreLogin, 1 профиль).

## Структура репо

```
app/
  orchestrator/       # пайплайн + шаги
  models/             # SQLAlchemy
  services/           # LLM / image / video / audio / ffmpeg / sheets
  bots/               # браузерные боты (chatgpt, outsee, 11labs, morelogin, soc)
  telegram/           # aiogram-бот, HITL-гейты
  errors/             # классификация + ретраи
  api/                # FastAPI (webhook, internal endpoints)
  cli/                # команды запуска
  settings.py         # конфиг
prompts/              # мастер-промты (PLAN_SHORTS, SCRIPT_SHORTS, IMAGE_SHORTS, ...)
data/                 # videos/<slug>/{characters, scenes, videos, audio, subs, final}
docker/               # Dockerfile-ы
docs/                 # архитектура, решения
tests/
```

## Быстрый старт (после полной сборки)

```bash
cp .env.example .env   # заполнить TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_CHAT_ID, ...
docker compose up -d
```

Telegram-бот оживает → пишешь `/new <тема>` → дальше он ведёт по этапам с HITL-подтверждениями.

## Портабельность на новый ПК

1. Скопировать папку проекта.
2. Установить Docker Desktop и MoreLogin.
3. Скопировать `./browser_profile/` (логины Chrome: outsee, ChatGPT, 11Labs, соцсети).
4. `docker compose up -d` — готово.
