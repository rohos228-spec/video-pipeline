# Архитектура video-pipeline

---

## Что это

Автоматический конвейер генерации коротких вертикальных роликов (60–75 сек, 9:16). Один Python-процесс на Windows, без Docker. Управление через Telegram-бот с кнопками одобрения на каждом шаге.

---

## Общая схема

```
Telegram-бот (aiogram 3)          Фоновый воркер
    │                                  │
    │  /menu → кнопки шагов           │  каждые 60 сек проверяет БД
    │  HITL: ✅ 🔁 ✏️ ❌               │  находит проекты в *_running
    │                                  │  вызывает advance_project()
    ▼                                  ▼
                   SQLite (data/state.db)
                          │
                          ▼
            Chrome (CDP порт 29229)
            ├── ChatGPT web (план, сценарий, промты)
            ├── outsee.io  (картинки, видео)
            └── elevenlabs (озвучка)
                          │
                          ▼
                FFmpeg + Whisper
                (сборка + субтитры)
```

---

## Структура файлов

```
app/
├── main.py                 # Точка входа: TG-бот + воркер в одном процессе
├── settings.py             # Конфиг из .env (pydantic-settings)
├── db.py                   # SQLite engine + session_scope
├── models.py               # ORM-модели: Project, Frame, Artifact, HITLRequest
├── seed_pilot.py           # Создание тестового проекта
├── worker.py               # Упрощённый воркер (основной — в main.py)
├── generation_options.py   # Генераторы, разрешения, aspect ratio (каталоги)
│
├── telegram/
│   ├── bot.py              # ВСЕ хендлеры бота (~7000 строк)
│   ├── menu.py             # Определения шагов (StepDef), клавиатуры
│   ├── mass_menu.py        # Меню массового создания
│   ├── wizard.py           # Мастер настроек (7 вопросов)
│   ├── prompt_picker.py    # Выбор мастер-промтов
│   └── test_prompt_menu.py # Меню тестирования промтов
│
├── orchestrator/
│   ├── pipeline.py         # advance_project() — роутер статусов
│   ├── auto_advance.py     # Авто-продвижение (массовый режим)
│   └── steps/
│       ├── make_plan.py        # Шаг 1: План (ChatGPT + xlsx)
│       ├── make_script.py      # Шаг 2: Закадровый текст
│       ├── split_frames.py     # Шаг 3: Разбивка на кадры
│       ├── generate_hero.py    # Шаг 4a: Персонажи
│       ├── generate_items.py   # Шаг 4b: Предметы
│       ├── enrich_xlsx.py      # Шаг 5: Доп работа с Excel (1–5 слотов)
│       ├── generate_image_prompts.py  # Шаг 6: Промты картинок
│       ├── generate_images.py  # Шаг 7: Генерация картинок (outsee)
│       ├── make_animation_prompts.py  # Шаг 8: Промты анимации
│       ├── generate_videos.py  # Шаг 9: Генерация видео (outsee)
│       ├── generate_audio.py   # Шаг 10: Озвучка
│       ├── assemble.py         # Шаг 11: Финальная сборка (FFmpeg)
│       └── publish.py          # Шаг 12: Публикация (MoreLogin)
│
├── bots/
│   ├── browser.py          # CDP-подключение к Chrome (BrowserSession)
│   ├── chatgpt.py          # Автоматизация ChatGPT web
│   ├── outsee.py           # Автоматизация outsee.io (картинки + видео)
│   ├── elevenlabs.py       # Автоматизация ElevenLabs TTS
│   └── publishers.py       # Публикация через MoreLogin (YouTube, VK, TikTok...)
│
├── services/
│   ├── hitl.py             # Отправка HITL-карточек (фото + кнопки)
│   ├── assembly.py         # FFmpeg: склейка видео + субтитры
│   ├── prompt_library.py   # Файловая библиотека мастер-промтов
│   ├── gpt_text_builder.py # Сборка «сопр. сообщения» для ChatGPT
│   ├── project_state.py    # Recompute статуса из данных
│   ├── reset_step.py       # Сброс шага (удаление данных + откат)
│   ├── batches.py          # Массовое создание (CRUD)
│   ├── test_prompt.py      # Тестирование визуальных промтов
│   ├── xlsx_sync.py        # Синхронизация xlsx ↔ БД (v7)
│   ├── xlsx_v8_import.py   # Импорт xlsx v8 → БД
│   ├── excel_characters.py # Парсинг персонажей из xlsx
│   ├── outsee_retry.py     # Retry-логика для outsee (3 + GPT rewrite + 3)
│   ├── auto_review.py      # GPT-проверка результатов (массовый режим)
│   ├── scan_frames.py      # Поиск кадров без картинок
│   ├── step_cancel.py      # Механизм отмены текущего шага
│   └── mass_pause.py       # Глобальная пауза массовой генерации
│
├── storage/
│   ├── __init__.py         # ProjectSheet — xlsx-обёртка
│   ├── project_sheet.py    # Чтение/запись project.xlsx (v8)
│   └── batch_sheet.py      # topics.xlsx для массового
│
├── monitor/                # Система мониторинга (отдельный модуль)
│   ├── __main__.py         # python -m app.monitor
│   ├── log_sink.py         # JSON-логи + событийный лог
│   ├── browser_watcher.py  # Скриншоты Chrome
│   ├── action_tracker.py   # Трекинг действий с таймингами
│   └── report.py           # Анализ и отчёт
│
prompts/                    # Мастер-промты (каждый шаг = папка)
│   ├── 01_plan/            # План
│   ├── 02_script/          # Закадровый текст
│   ├── 03_razbivka/        # Разбивка на блоки
│   ├── 04_hero/            # Персонажи (turnaround sheet шаблон)
│   ├── 04_hero_style/      # Стиль персонажей (антропоморфные коты и т.д.)
│   ├── 04b_items/          # Предметы
│   ├── 05a_enrich_1/..05e_enrich_5/  # Доп работа с Excel (5 слотов)
│   ├── 05_image_prompts/   # Промты картинок
│   ├── 07_animation/       # Промты анимации
│   └── check_*/            # Промты для авто-ревью (GPT-проверка)
│
data/                       # Рабочие данные (не в git)
│   ├── state.db            # SQLite база
│   ├── videos/<slug>/      # Артефакты одиночного проекта
│   │   ├── project.xlsx
│   │   ├── characters/     # Референсы персонажей
│   │   ├── scenes/         # Картинки кадров
│   │   ├── videos/         # 8-сек клипы
│   │   ├── audio/          # Озвучка
│   │   ├── subs/           # Субтитры
│   │   └── final/          # Финальный MP4
│   ├── batches/<slug>/     # Массовый проект
│   │   ├── topics.xlsx
│   │   └── sub/<sub_slug>/ # Подпроекты
│   └── monitor/            # Данные мониторинга
```

---

## Пайплайн: 12 шагов

| # | Шаг | Что делает | Инструмент |
|---|-----|-----------|------------|
| 1 | **План** | Общий план ролика | ChatGPT + xlsx |
| 2 | **Закадровый текст** | Сценарий озвучки | ChatGPT → voiceover.txt |
| 3 | **Разбивка** | Нарезка на 15–30 кадров по 2–4 сек | ChatGPT + xlsx |
| 4a | **Персонажи** | Референс ГГ (turnaround sheet) | ChatGPT → outsee.io |
| 4b | **Предметы** | Референсы предметов | ChatGPT → outsee.io |
| 5 | **Доп работа с Excel** | 1–5 раундов xlsx round-trip | ChatGPT + xlsx |
| 6 | **Промты картинок** | Промт для каждого кадра | ChatGPT + xlsx |
| 7 | **Картинки** | Генерация картинки для каждого кадра | outsee.io (nano-banana) |
| 8 | **Промты анимации** | Промт анимации для каждого кадра | ChatGPT |
| 9 | **Видео** | 8-сек клип для каждого кадра | outsee.io (veo-3-fast) |
| 10 | **Аудио** | Озвучка закадрового текста | ElevenLabs |
| 11 | **Сборка** | Склейка клипов + субтитры + аудио | FFmpeg + faster-whisper |
| 12 | **Публикация** | Загрузка на площадки | MoreLogin |

Каждый шаг имеет два статуса:
- `*_running` — воркер выполняет
- `*_ready` — ждёт одобрения пользователя

---

## Telegram-бот: команды

| Команда | Что делает |
|---------|-----------|
| `/start` | Приветствие + подсказки |
| `/menu` | Главное меню с кнопками |
| `/status` | Список проектов и их статусы |

### Главное меню

| Кнопка | Действие |
|--------|---------|
| 📁 Новый проект | Создать проект → мастер настроек (7 вопросов) |
| 📋 Существующие проекты | Список проектов |
| 🎬 Массовое создание | Батчи (автоматический режим) |
| 🧪 Тест промтов | Тестирование визуальных промтов |
| ⏸ / ▶ Пауза массовой | Приостановить/возобновить все батчи |

### Меню проекта

| Кнопка | Действие |
|--------|---------|
| Шаги 1–11 | Запуск / просмотр каждого шага |
| ⚙ Настройки | Генератор, разрешение, aspect ratio |
| 🧰 Промты | Выбор/редактирование мастер-промтов |
| ⏹ Остановить | Прервать текущий шаг |
| 📥 Скачать xlsx | Получить project.xlsx |
| 🔄 Перечитать xlsx | Обновить БД из xlsx |
| 🗑 Удалить | Удалить проект |

### HITL-кнопки (на каждом шаге)

| Кнопка | Действие |
|--------|---------|
| ✅ | Одобрить и перейти дальше |
| 🔁 | Перегенерировать (outsee «Повторить», без ChatGPT) |
| ✏️ | Редактировать промт (ответить новым текстом) |
| ❌ | Отклонить |

---

## Настройки проекта (мастер при создании)

| Вопрос | Варианты |
|--------|---------|
| Генератор картинок | Nano Banana 2, Pro, Seedream, GPT Image... (7 шт) |
| Соотношение сторон | 9:16, 16:9, 1:1, 4:3, 3:4... (8 шт) |
| Разрешение картинок | 2K, 4K |
| Relax картинки | Да / Нет |
| Генератор видео | Kling, Veo, Seedance, Wan, Hailuo... (14 шт) |
| Разрешение видео | 720p, 1080p |
| Relax видео | Да / Нет (только для Veo) |

---

## Массовое создание

1. Главное меню → 🎬 Массовое создание → Новый батч
2. Добавить темы (текстом или через topics.xlsx)
3. Настроить: auto_mode, enrich_slots, hero_count и т.д.
4. ▶ Запустить очередь — подпроекты обрабатываются по одному
5. GPT автоматически ревьювит результаты (без участия человека)

---

## Конфиг (.env)

| Переменная | Что | Обязательна |
|-----------|-----|:-----------:|
| `TELEGRAM_BOT_TOKEN` | Токен бота от @BotFather | ✅ |
| `TELEGRAM_OWNER_CHAT_ID` | Твой chat_id | ✅ |
| `TELEGRAM_PROXY_URL` | Прокси для Telegram (если заблокирован) | — |
| `BROWSER_CDP_URL` | URL Chrome CDP (дефолт: localhost:29229) | — |
| `SQLITE_PATH` | Путь к БД (дефолт: ./data/state.db) | — |
| `OUTSEE_IMAGE_URL` | URL outsee для картинок | — |
| `OUTSEE_VIDEO_URL` | URL outsee для видео | — |
| `SOCIAL_PUBLISH_ENABLED` | Включить публикацию (дефолт: false) | — |
| `MORELOGIN_PROFILE_ID` | ID профиля MoreLogin | — |
| `WHISPER_MODEL` | Модель Whisper (tiny/base/small/medium/large-v3) | — |
| `LOG_LEVEL` | Уровень логов (дефолт: INFO) | — |
| `HITL_AUTO_APPROVE` | Авто-одобрение всех шагов (дефолт: false) | — |

---

## Зависимости

- **Python 3.11–3.12** (не 3.13)
- **FFmpeg** (в PATH)
- **Chrome** (с `--remote-debugging-port=29229`)
- Логин в Chrome: ChatGPT, outsee.io, (опц.) ElevenLabs
- (опц.) MoreLogin — для публикации на площадки
