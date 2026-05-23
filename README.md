# video-pipeline

Автоматический конвейер генерации коротких роликов (60–75 сек, 9:16) с оркестрацией, **веб-студией** (HITL и запуск шагов) и опциональным Telegram-ботом. Интеграции: outsee.io (Nano Banana 2 + Veo 3.1 Fast Relax), ChatGPT web, 11Labs, Whisper, FFmpeg и MoreLogin.

## Стек

- Python 3.11 (pure Python, без Docker)
- SQLite + aiosqlite (локальное состояние)
- SQLAlchemy 2 + Alembic (ORM + миграции)
- aiogram 3 (Telegram-бот) + FastAPI (внутренний API / webhook)
- Playwright (async, через CDP `localhost:29229` к существующему Chrome пользователя)
- faster-whisper (локальный Whisper для субтитров)
- ffmpeg-python (обёртка над FFmpeg)
- pydantic-settings (конфиги), loguru (логи)

## Ключевые решения

- **Без Docker.** Всё крутится напрямую на Windows/macOS/Linux, `python -m app.main` — и всё.
- **Короткий формат:** 60–75 сек, вертикаль 9:16, кадры по 2–4 сек, 15–30 кадров на ролик, 1000–1300 знаков текста.
- **1 слой:** все кадры одиночные, без «сборных сцен».
- **БД — источник правды.** Excel генерится экспортом по запросу из Telegram-бота.
- **HITL-гейты** по умолчанию: концепт/план, сценарий, референс ГГ (если нужен), все сцены перед анимацией, все видео перед сборкой, финальный ролик перед публикацией.
- **Площадки:** TikTok, YouTube Shorts, Instagram Reels, VK Клипы, Likee (через MoreLogin, 1 профиль).

## Структура репо

```
app/
  orchestrator/       # пайплайн + шаги
  models.py           # SQLAlchemy
  services/           # hitl / whisper / mapper / assembly / prompts
  bots/               # браузерные боты (chatgpt, outsee, elevenlabs, morelogin, publishers)
  telegram/           # aiogram-бот, HITL-гейты
  settings.py         # конфиг
  db.py               # SQLite engine + session
  main.py             # запуск TG-бота
  worker.py           # фоновый воркер
prompts/              # мастер-промты (PLAN_SHORTS, SCRIPT_SHORTS, IMAGE_SHORTS, ...)
data/                 # videos/<slug>/{characters, scenes, videos, audio, subs, final}
```

## Быстрый старт (веб-студия, без Telegram)

```bash
pip install -e ".[dev]"
cp .env.example .env          # TELEGRAM_ENABLED=false по умолчанию
./start-studio.sh             # воркер + API :8765
# второй терминал:
cd web && npm install && npm run dev   # UI http://localhost:3000
```

С Telegram: задайте `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ENABLED=true`, `python -m app.main` или `.\start.ps1`.

Подробная инструкция — см. [HOW_TO_RUN.md](HOW_TO_RUN.md).

## Портабельность на новый ПК

1. Скопировать папку проекта.
2. Поставить Python 3.11+ и выполнить `pip install -e .`.
3. Запустить Chrome с `--remote-debugging-port=29229` и залогиниться во все сервисы.
4. `python -m app.main`.
