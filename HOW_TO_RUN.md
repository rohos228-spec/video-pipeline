# Как запустить бот у себя (простая инструкция)

Вся система работает на твоём ПК. Telegram-бот оживает, когда ты запускаешь
Docker. Выключил Docker — бот не отвечает.

---

## Один раз (первоначальная настройка)

### 1. Поставь Docker Desktop

- Windows / macOS: https://www.docker.com/products/docker-desktop/  → Install.
- После установки запусти Docker Desktop и подожди, пока в трее появится
  «Docker is running» (зелёный значок-кит).

### 2. Поставь MoreLogin (если ещё нет)

- https://www.morelogin.com/ → скачай клиент для Windows.
- Создай один браузерный профиль и зайди в нём в:
  TikTok, YouTube Studio, Instagram, VK, Likee, ChatGPT, outsee.io, 11Labs.
- В настройках MoreLogin включи «Local API» (обычно уже включено по
  умолчанию) и запомни `profileId` нужного профиля — длинная строка рядом
  с именем профиля.

### 3. Скачай проект

```bash
git clone https://github.com/rohos228-spec/video-pipeline.git
cd video-pipeline
```

Если git не установлен — на странице репо нажми **Code → Download ZIP**,
распакуй в любую папку, зайди в неё в терминале.

### 4. Создай `.env`

Скопируй файл `.env.example` в `.env`:
```bash
cp .env.example .env
```
Открой `.env` любым текстовым редактором и заполни:
```
TELEGRAM_BOT_TOKEN=  # сюда вставь токен бота от @BotFather
TELEGRAM_OWNER_CHAT_ID=279887118   # твой chat_id (уже заполнен)
MORELOGIN_PROFILE_ID=              # profileId из MoreLogin
SOCIAL_PUBLISH_ENABLED=false       # пока оставь false — сначала проверяем, потом включим
```

### 5. Запусти Chrome с включённым «ремоут-портом» для Playwright

Скопируй команду и запусти в PowerShell (Windows) один раз:

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=29229 `
  --user-data-dir="$env:USERPROFILE\.vp_browser_data"
```

На macOS/Linux:
```bash
google-chrome --remote-debugging-port=29229 \
  --user-data-dir="$HOME/.vp_browser_data"
```

Откроется **отдельный** Chrome с чистым профилем. **Залогинься в нём руками**
в:
- https://chatgpt.com/
- https://outsee.io/
- https://elevenlabs.io/

Эти логины сохранятся в папке `~/.vp_browser_data` и будут подхватываться
каждый раз.

⚠️ Этот Chrome должен быть **открыт** всё время, пока крутится бот.

---

## Каждый раз (когда хочешь работать)

### 1. Запусти Chrome (если закрыл)
Тот же самой командой из пункта 5 выше.

### 2. Запусти Docker-бот:
```bash
cd video-pipeline
docker compose up -d
```
Это поднимет Postgres + Telegram-бот + фоновый воркер в фоне.

Чтобы увидеть логи:
```bash
docker compose logs -f
```

### 3. В Telegram пиши своему боту

- `/start` — проверка связи, бот ответит приветствием.
- `/new <тема>` — начать ролик. Примеры:
  - `/new Как коты завоевали Интернет`
  - `/new История дружбы кота и собаки --no-hero` (без главного героя)
  - `/new Путешествие кота-космонавта --hero` (принудительно генерить героя)

Дальше бот будет присылать тебе:
1. **Общий план ролика** → жмёшь [✅ Одобрить] / [🔁 Перегенерировать] / [❌ Отклонить].
2. **Сценарий** → жмёшь кнопку.
3. **Референс главного героя** (картинка) — если он нужен.
4. **Готовые картинки кадров** (уведомление, папка — `data/videos/<slug>/scenes/`).
5. **Готовые 8-сек клипы** (уведомление, папка — `data/videos/<slug>/videos/`).
6. **Финальный собранный ролик** (mp4 в Telegram) → одобряешь → публикация на 5 площадок (если `SOCIAL_PUBLISH_ENABLED=true`).

### 4. Остановить
```bash
docker compose down
```

---

## Если что-то сломалось

- **Бот не отвечает в Telegram** → проверь, что `docker compose ps` показывает все 3 контейнера как `Up`, и в `docker compose logs app` нет ошибок подключения.
- **Первый шаг висит, нет плана** → скорее всего не запущен Chrome с `--remote-debugging-port=29229` или ты не залогинен в ChatGPT.
- **Ошибка про selector** → какая-то страница поменяла UI. Запусти режим разведки (см. ниже) и пришли мне лог.

### Режим разведки селекторов
В отдельном терминале (Docker не нужен):
```bash
cd video-pipeline
python -m app.bots.outsee recon-image "тест"
python -m app.bots.elevenlabs recon "тест"
python -m app.bots.publishers recon tiktok
```
Скрипт откроет страницу и напечатает все кнопки/текстареа с их атрибутами.
Скопируй вывод и пришли в чат — я обновлю селекторы.

---

## Переезд на другой ПК

1. Скопируй папку `video-pipeline/` на новый ПК.
2. Скопируй папку с профилем Chrome — ту, что указана в `--user-data-dir`
   (например, `~/.vp_browser_data`).
3. Поставь Docker Desktop + MoreLogin на новом ПК.
4. `docker compose up -d`.

Готово.
