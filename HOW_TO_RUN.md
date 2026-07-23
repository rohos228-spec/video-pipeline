# Как запустить video-pipeline (Windows)

## Единственная точка входа — `STUDIO.cmd`

1. Открой папку `video-pipeline\` в Проводнике.
2. **Дважды кликни `STUDIO.cmd`**.
3. Выбери пункт меню:

| Пункт | Действие |
|-------|----------|
| **1** | **Запустить студию** — бэкенд + Chrome CDP :29229 + браузер http://127.0.0.1:8765 |
| **2** | **Остановить всё** — остановить бэкенд (порт 8765); Chrome с ИИ **не** закрывается |
| **3** | **Браузер с ИИ** — Chrome CDP :29229 с профилем из `VpBrowserProfile.ps1` / `.env`; вкладки outsee.io и chatgpt.com. Если CDP уже жив — второе окно не открывается |
| **4** | **Обновить и запустить** — `git stash` → `fetch` → `reset --hard origin/main` → зависимости → пункт 1 |
| **5** | **Починить установку** — pip, npm build web, Playwright, FFmpeg |
| **6** | **Диагностика** — версия, git, порты, Chrome-профиль; лог `logs/doctor.log` |
| **0** | Выход |

Из корня репозитория для автоматизации:

```powershell
.\STUDIO.cmd 1    # запуск
.\STUDIO.cmd 2    # остановить бэкенд
.\STUDIO.cmd 3    # браузер с ИИ
```

Ярлык на рабочий стол (один раз): `create-desktop-shortcut.cmd`

---

## Первая установка на новый ПК

```powershell
iwr https://raw.githubusercontent.com/rohos228-spec/video-pipeline/refs/heads/main/bootstrap.ps1 -UseBasicParsing | iex
```

Или вручную: `install.ps1` в корне репозитория.

После установки — **STUDIO.cmd** → **1**.

---

## Chrome для пайплайна (ChatGPT / outsee)

Профиль по умолчанию: `%USERPROFILE%\.vp_browser_data`  
Переопределение: `BROWSER_USER_DATA_DIR` в `.env` (см. `scripts/VpBrowserProfile.ps1`).

**STUDIO.cmd → 3** — запуск Chrome с CDP и вкладками outsee.io + chatgpt.com.  
Залогинься один раз; сессии сохраняются в профиле. Окно держи открытым во время генерации.

Пункт **1** также поднимает CDP, если он ещё не запущен.

---

## Telegram (опционально)

В `.env`: `TELEGRAM_BOT_TOKEN` и `TELEGRAM_ENABLED=true`. Запуск с ботом — `python -m app.main` в `.venv`.

Для веб-студии без Telegram: `TELEGRAM_ENABLED=false` (по умолчанию после `install.ps1`).

---

## Linux / разработка

```bash
pip install -e ".[dev]"
python3 -m app.main
```

Студия: http://127.0.0.1:8765

---

## Что не трогает обновление (пункт 4)

Папки `data/`, `prompts/`, `logs/` и файл `.env` в `.gitignore` — `git reset` их не перезаписывает. Локальные правки в отслеживаемых файлах перед обновлением сохраняются в `git stash`.

---

## Архив старых скриптов

Перенесены в `scripts/legacy/`: `BACKEND.cmd`, `BROWSER-OUTSEE.cmd`, `Diagnose-Chrome.cmd`, `OBNOVIT-I-ZAPUSK.cmd`, `PULL-HOTFIX.cmd` и др.

**Запуск** — только **STUDIO.cmd** (пункты 1–6).

---

## Ошибка парсинга `scripts\studio.ps1`

Обнови репозиторий:

```powershell
git checkout main
git pull origin main
.\STUDIO.cmd
```

Обходной путь: `.\scripts\run-backend.ps1` → http://127.0.0.1:8765
