# Handover — для следующей Devin-сессии

Этот файл позволяет продолжить работу над проектом в новой сессии Devin
(в том числе на другом аккаунте) без потери контекста. **Пришли этот файл
новой сессии одним сообщением — и дай команду «продолжай».**

## Коротко про проект

Полностью автоматический конвейер для создания вертикальных шортсов
60-75 сек с HITL-одобрением на каждом критическом шаге. Запускается через
Telegram-бот. Все шаги идут в одном слое (без композитных сцен), персонажи —
антропоморфные коты.

## Что готово (4 PR созданы, мёрджатся стэком 1→2→3→4)

| PR  | URL                                                    | Ветка                        | Base                     | Что внутри                                              |
|-----|--------------------------------------------------------|------------------------------|--------------------------|---------------------------------------------------------|
| 1   | https://github.com/rohos228-spec/video-pipeline/pull/1 | `devin/wire-text-steps`      | `main`                   | ChatGPT web-бот, шаги plan → script → split_frames, HITL-хелпер, воркер + бот, мастер-промты |
| 2   | https://github.com/rohos228-spec/video-pipeline/pull/2 | `devin/outsee-bots`          | `devin/wire-text-steps`  | outsee.io-бот (nano-banana-2 + veo-3-fast), шаги hero/images/animation-prompts/videos |
| 3   | https://github.com/rohos228-spec/video-pipeline/pull/3 | `devin/audio-assemble`       | `devin/outsee-bots`      | 11Labs-бот, faster-whisper, FFmpeg-сборка финала, шаги generate_audio/assemble |
| 4   | https://github.com/rohos228-spec/video-pipeline/pull/4 | `devin/publish-morelogin`    | `devin/audio-assemble`   | MoreLogin-пускач, 5 publishers (TT/YT/IG/VK/Likee), шаг publish |

**main** сейчас = initial skeleton. После мёрджа всех PR в порядке 1→2→3→4
`main` будет иметь полный рабочий пайплайн.

## Решения, которые зафиксированы

- **Формат**: вертикаль 9:16, 60-75 сек, кадры 2-4 сек, 15-30 кадров, 1 слой,
  ~1000-1300 знаков, ячейка = 40-110 символов.
- **Персонажи**: антропоморфные коты. Главный герой — опциональный
  (`/new <тема> [--hero | --no-hero | --auto]`, дефолт — auto).
- **Локации данных**: Postgres (контейнер) + `./data/videos/<slug>/{characters,scenes,videos,audio,subs,final}/` (volume).
- **Мастер-промты**: `prompts/*.vN.md` файлы, автозагружаются в БД при старте
  воркера через `app/prompts_loader.py`. Версия = суффикс `vN`.
- **HITL-гейты**: approve_plan, approve_script, approve_hero, approve_images,
  approve_videos, approve_final.
- **Публикация**: 1 профиль MoreLogin → 5 соцсетей.
- **Excel**: БД как источник правды, экспорт в xlsx по запросу (не реализовано
  ещё — будет отдельным PR после мёрджа).

## Что НЕ сделано / пока черновик

- **Мастер-промт `VIDEO_SHORTS.v1.md`** — черновик. Пользователь сказал:
  «пока оставь пустым, позже пришлю». Когда пришлёт — заменить содержимое и
  увеличить версию в имени файла (`VIDEO_SHORTS.v2.md`).
- **Селекторы UI** для outsee / 11Labs / publishers — кандидаты, **на живую не
  проверены**. Для калибровки в каждом из файлов есть CLI-режим:
  `python -m app.bots.outsee recon-image "тест"`,
  `python -m app.bots.elevenlabs recon "тест"`,
  `python -m app.bots.publishers recon <platform>`.
  При первом реальном запуске попросить пользователя прислать вывод, подправить
  константы в начале файлов.
- **Точечная регенерация одного кадра** не реализована — сейчас regenerate
  откатывает сразу весь набор на предыдущую стадию.
- **Экспорт в xlsx** (кнопка в Telegram) не реализован.
- **Word-by-word субтитры в ASS** не реализованы — сейчас простые статические
  на фрейм.

## Критические внешние контексты

- **Telegram**:
  - Токен бота — в секретах Devin как `TELEGRAM_BOT_TOKEN` (пользователь выдал
    в защищённом канале; в открытом чате токена нет).
  - `chat_id` владельца: `279887118`.
- **GitHub**:
  - Репо: `rohos228-spec/video-pipeline` (приватный, создан пользователем).
  - Авторизация Devin уже настроена — пуш идёт через
    `https://git-manager.devin.ai/proxy/github.com/...` автоматически.
- **Local-only зависимости** (не могут работать на VM Devin):
  - Chrome с `--remote-debugging-port=29229` — у пользователя на ПК.
  - MoreLogin клиент — только Windows, API на `http://127.0.0.1:40000`.
  - Платёжные аккаунты в outsee.io, 11Labs, MoreLogin.
- **Эксель пользователя**: исходный шаблон в `~/attachments/.../3.xlsx` (если
  нужно перечитать). Мастер-промты из него уже распарсены в `prompts/*.v1.md`.

## Структура кода

```
app/
  bots/
    browser.py            — базовый Playwright CDP коннектор
    chatgpt.py            — ChatGPT web
    outsee.py             — outsee.io (nano-banana-2 + veo-3-fast)
    elevenlabs.py         — 11Labs web
    morelogin.py          — MoreLogin API + Playwright
    publishers.py         — TikTok/YT/IG/VK/Likee
  orchestrator/
    pipeline.py           — стейт-машина advance_project(session, project, bot)
    steps/
      make_plan.py        — планирование (ChatGPT)
      make_script.py      — сценарий (ChatGPT)
      split_frames.py     — разбиение на кадры
      generate_hero.py    — референс ГГ (ChatGPT + outsee)
      generate_images.py  — картинки кадров (ChatGPT + outsee)
      make_animation_prompts.py — промты анимации (ChatGPT)
      generate_videos.py  — клипы кадров (outsee veo)
      generate_audio.py   — озвучка + whisper (11Labs + faster-whisper)
      assemble.py         — финал (FFmpeg + ASS)
      publish.py          — публикация (MoreLogin + publishers)
  services/
    prompts.py            — загрузка активного промта по ключу
    hitl.py               — send_hitl_text/photo/video + wait_for_decision
    whisper.py            — faster-whisper обёртка
    mapper.py             — whisper-слова → таймкоды кадров
    assembly.py           — FFmpeg concat + subs
  telegram/
    bot.py                — aiogram 3 (/start, /new, /status, callbacks)
  db.py, models.py, settings.py, prompts_loader.py, worker.py, main.py
prompts/
  PLAN_SHORTS.v1.md
  SCRIPT_SHORTS.v1.md
  IMAGE_SHORTS.v1.md
  VIDEO_SHORTS.v1.md      — ЧЕРНОВИК, заменить когда придёт финальная версия
docker-compose.yml, docker/Dockerfile, pyproject.toml, .env.example
HOW_TO_RUN.md             — инструкция для пользователя
HANDOVER.md               — этот файл
```

## Следующие шаги (приоритет)

1. **Дождаться мёрджа 4 PR** в порядке 1→2→3→4 (GitHub должен автоматически
   переподтянуть base после каждого merge).
2. **Калибровка селекторов** — только после реального запуска у пользователя.
   Попросить прогнать recon-команды и прислать логи.
3. **Получить финальный `VIDEO_SHORTS`** от пользователя, заменить в
   `prompts/VIDEO_SHORTS.v1.md` или добавить `v2.md`.
4. **Добавить `/export <id>`** в Telegram-бот — выгрузка проекта в xlsx по
   его шаблону (лист `план` из `3.xlsx`).
5. **Добавить notifications on exception** в worker — если
   `advance_project` бросает исключение, слать пользователю
   «Ошибка на шаге X: {msg}» чтобы он видел, что бот застрял.
6. **Точечная регенерация** кадра через команду `/regen <project_id> <frame>`.

## Как не сломать

- **Не форс-пушить** в main и в уже замёрдженные ветки.
- **Не менять** `models.py` schema без миграции (сейчас стоит
  `Base.metadata.create_all` при старте — для новых таблиц работает,
  для ALTER — нет; понадобится — добавить alembic).
- **Не коммитить `.env` / `data/` / `browser_profile/`** — все уже в
  `.gitignore`.

## Контакты

Пользователь: Sanёcheck, GitHub `rohos228-spec`, email `rohos228@gmail.com`.
Общение в TG через бота + через чат этой Devin-сессии.
