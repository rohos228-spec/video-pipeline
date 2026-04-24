# Как запустить бот у себя (Windows, без Docker)

Вся система работает на твоём ПК как обычный Python-процесс.
Telegram-бот оживает, когда ты запускаешь `python -m app.main`.
Закрыл консоль — бот не отвечает.

---

## Один раз (первоначальная настройка)

### 1. Поставь Python 3.11 или 3.12

Скачай с python.org. При установке поставь галочку «Add Python to PATH».

Проверь в PowerShell:
```powershell
python --version
# Python 3.11.x
```

### 2. Поставь FFmpeg

- Скачай https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip
- Распакуй куда-нибудь (например, `C:\ffmpeg`).
- Добавь `C:\ffmpeg\bin` в PATH (Система → Переменные среды → Path → Add).
- Проверь: `ffmpeg -version` в PowerShell.

### 3. (Опционально сейчас, нужно для публикации позже) Поставь MoreLogin

- https://www.morelogin.com/ → скачай клиент для Windows.
- Создай один браузерный профиль и зайди в нём в:
  TikTok, YouTube Studio, Instagram, VK, Likee.
- В настройках MoreLogin включи «Local API» и запомни `profileId` профиля.

Если сейчас тебе это не нужно (публикация отключена по умолчанию) — пропусти шаг.

### 4. Скачай проект

```powershell
git clone https://github.com/rohos228-spec/video-pipeline.git
cd video-pipeline
```

Если git не установлен — на странице репо нажми **Code → Download ZIP**,
распакуй в любую папку, зайди в неё в терминале.

### 5. Создай виртуальное окружение и поставь зависимости

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

Первая установка скачает ~1 GB (faster-whisper + ffmpeg-python + playwright).

### 6. Создай `.env`

```powershell
Copy-Item .env.example .env
notepad .env
```

Заполни:
```
TELEGRAM_BOT_TOKEN=  # токен бота от @BotFather (или уже в секретах Devin)
TELEGRAM_OWNER_CHAT_ID=279887118   # твой chat_id, уже заполнен
MORELOGIN_PROFILE_ID=              # profileId из MoreLogin — можно оставить пустым
SOCIAL_PUBLISH_ENABLED=false       # публикация выключена до отдельного решения
```

### 7. Запусти Chrome с «ремоут-портом» для Playwright

В PowerShell (один раз — пока работает бот):

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=29229 `
  --user-data-dir="$env:USERPROFILE\.vp_browser_data"
```

Откроется **отдельный** Chrome с чистым профилем. **Залогинься в нём руками**
в:
- https://chatgpt.com/
- https://outsee.io/
- https://elevenlabs.io/ (опционально — пока не трогаем)

Эти логины сохранятся в папке `%USERPROFILE%\.vp_browser_data` и будут подхватываться
каждый раз, когда ты запускаешь этот Chrome той же командой.

⚠️ Этот Chrome должен быть **открыт** всё время, пока крутится бот.

---

## Каждый раз (когда хочешь работать)

### 1. Запусти Chrome (если закрыл)

Тот же командой из пункта 7 выше.

### 2. Запусти бот

```powershell
cd video-pipeline
.\.venv\Scripts\Activate.ps1
python -m app.main
```

Это стартует Telegram-бот + фоновый воркер. Логи льются в консоль.

### 3. В Telegram пиши своему боту

- `/start` — проверка связи.
- `/new <тема>` — начать ролик. Примеры:
  - `/new Как коты завоевали Интернет`
  - `/new История дружбы кота и собаки --no-hero`
  - `/new Путешествие кота-космонавта --hero`

Или можно сразу запустить пилотный проект:
```powershell
python -m app.seed_pilot
```
Это создаст проект «5 фактов о рачках в стиле киберпанк» и воркер начнёт его прогонять.

Дальше бот будет присылать тебе:
1. **Общий план ролика** → [✅ Одобрить] / [🔁 Перегенерировать] / [❌ Отклонить].
2. **Сценарий** → кнопка.
3. **Референс главного героя** (если нужен).
4. **Готовые картинки кадров** (уведомление, папка — `data/videos/<slug>/scenes/`).
5. **Готовые 8-сек клипы** (уведомление, папка — `data/videos/<slug>/videos/`).
6. **Финальный собранный ролик** (mp4 в Telegram) → одобряешь → публикация на 5 площадок (если `SOCIAL_PUBLISH_ENABLED=true`).

### 4. Остановить

Ctrl+C в консоли, где крутится `python -m app.main`.

---

## Если что-то сломалось

- **Бот не отвечает в Telegram** → в консоли ищи `ERROR` / `Traceback`. Пришли скрин.
- **Первый шаг висит, нет плана** → скорее всего не запущен Chrome с `--remote-debugging-port=29229` или ты не залогинен в ChatGPT.
- **Ошибка про selector** → сайт поменял UI. Запусти режим разведки (см. ниже) и пришли вывод.

### Режим разведки селекторов

В отдельном терминале (бот всё так же должен крутиться):
```powershell
cd video-pipeline
.\.venv\Scripts\Activate.ps1
python -m app.bots.outsee recon-image "тест"
python -m app.bots.elevenlabs recon "тест"
python -m app.bots.publishers recon tiktok
```
Скрипт откроет страницу и напечатает все кнопки/textarea с атрибутами.
Скопируй вывод, пришли в чат — я обновлю селекторы.

---

## Переезд на другой ПК

1. Скопируй папку `video-pipeline/` на новый ПК.
2. Скопируй папку с профилем Chrome (`%USERPROFILE%\.vp_browser_data`).
3. Поставь Python + FFmpeg по пунктам 1-2.
4. `pip install -e .` → `.env` → запусти Chrome → `python -m app.main`.

Готово.
