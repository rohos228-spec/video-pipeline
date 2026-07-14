# Как запустить video-pipeline (Windows)

## Быстрый старт

1. Открой папку `video-pipeline\` в Проводнике.
2. **Дважды кликни `STUDIO.cmd`**.
3. В меню выбери нужный пункт:
   - **1** — запустить студию (бэкенд + веб + браузер на http://127.0.0.1:8765)
   - **2** — обновить с GitHub (`origin/main`) и запустить
   - **3** — починить установку (зависимости, сборка UI, Playwright, FFmpeg)
   - **4** — диагностика (версия, git, порты; лог в `logs/doctor.log`)

Ярлык на рабочий стол (один раз): `create-desktop-shortcut.cmd`

---

## Первая установка на новый ПК

В PowerShell:

```powershell
iwr https://raw.githubusercontent.com/rohos228-spec/video-pipeline/refs/heads/main/bootstrap.ps1 -UseBasicParsing | iex
```

Или вручную: `install.ps1` в корне репозитория.

После установки — **STUDIO.cmd** → пункт **1**.

---

## Chrome для пайплайна (ChatGPT / outsee)

Перед первым шагом с ботами запусти Chrome с CDP (пункт **1** может сделать это автоматически):

```powershell
& "$env:ProgramFiles\Google\Chrome\Application\chrome.exe" `
  --remote-debugging-port=29229 `
  --user-data-dir="$env:USERPROFILE\.vp_browser_data"
```

Залогинься в ChatGPT и outsee.io в этом окне. Окно держи открытым во время работы.

---

## Telegram (опционально)

В `.env`: `TELEGRAM_BOT_TOKEN` и `TELEGRAM_ENABLED=true`. Запуск с ботом — через `python -m app.main` в активированном `.venv` (режим разработки).

Для веб-студии без Telegram: `TELEGRAM_ENABLED=false` (по умолчанию после `install.ps1`).

---

## Linux / разработка

```bash
pip install -e ".[dev]"
python3 -m app.main
```

Студия: http://127.0.0.1:8765

---

## Что не трогает обновление (пункт 2)

Папки `data/`, `prompts/`, `logs/` и файл `.env` в `.gitignore` — `git reset` их не перезаписывает. Локальные правки в отслеживаемых файлах перед обновлением сохраняются в `git stash`.

---

## Старые скрипты

Перенесены в `scripts/legacy/` (архив): `BACKEND.cmd`, `START-STUDIO.cmd`, `Open-Studio.cmd`, `check-backend.cmd`, `stop-backend.cmd` и др.

**Запуск бэкенда** — только через **STUDIO.cmd** (пункт 1) или `scripts/run-backend.ps1`.

---

## Ошибка парсинга `scripts\studio.ps1` (кракозябры / Missing '}' )

Обычно это старая копия без UTF-8 BOM или без `git pull`. Обнови репозиторий и перезапусти:

```powershell
cd C:\Users\Admin\Desktop\video-pipeline
git fetch origin
git pull
.\STUDIO.cmd
```

Обходной путь без меню:

```powershell
.\scripts\run-backend.ps1
```

Браузер: http://127.0.0.1:8765
