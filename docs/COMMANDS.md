# Команды video-pipeline (PowerShell)

---

## Первая установка (новый ПК)

```powershell
# Автоматическая (один скрипт — ставит Git, Python, FFmpeg, venv, зависимости):
iwr https://raw.githubusercontent.com/rohos228-spec/video-pipeline/refs/heads/devin/windows-installer/bootstrap.ps1 -UseBasicParsing | iex
```

### Ручная установка

```powershell
# 1. Клонировать
git clone https://github.com/rohos228-spec/video-pipeline.git
cd video-pipeline

# 2. Venv + зависимости
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .

# 3. Настройки
Copy-Item .env.example .env
notepad .env
# Заполнить TELEGRAM_BOT_TOKEN=...
```

---

## Каждый запуск

### 1. Запустить Chrome (с отладочным портом)

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=29229 --user-data-dir="$env:USERPROFILE\.vp_browser_data"
```

При первом запуске залогиниться в Chrome:
- https://chatgpt.com/
- https://outsee.io/

### 2. Запустить бот

```powershell
cd video-pipeline
.\.venv\Scripts\Activate.ps1
python -m app.main
```

Или через скрипт:
```powershell
.\start.ps1
```

### 3. Остановить

`Ctrl+C` в консоли.

---

## Режим мониторинга (отладка)

```powershell
# Вместо python -m app.main:
python -m app.monitor

# Скриншоты каждые 5 сек:
python -m app.monitor --interval 5

# Без скриншотов (только логи):
python -m app.monitor --no-browser
```

### Отчёт

```powershell
# За сегодня:
python -m app.monitor.report

# За конкретный день:
python -m app.monitor.report --date 2026-05-23

# В формате JSON:
python -m app.monitor.report --json
```

Данные мониторинга:
- `data/monitor/logs/` — полные логи
- `data/monitor/events/` — события с таймингами
- `data/monitor/screenshots/` — скриншоты Chrome

---

## Пилотный проект (тест пайплайна)

```powershell
# Сбросить БД и создать пилот
Remove-Item -Force data\state.db
python -m app.seed_pilot

# Запустить бот — воркер подхватит пилот
python -m app.main
```

---

## Проверка статуса проекта (SQLite)

```powershell
python -c "import sqlite3; print(sqlite3.connect('data/state.db').execute('SELECT id, status FROM projects').fetchall())"
```

---

## Разведка селекторов (если сайт поменял UI)

```powershell
python -m app.bots.outsee recon-image "тест"
python -m app.bots.elevenlabs recon "тест"
python -m app.bots.publishers recon tiktok
```

---

## Обновление кода

```powershell
cd video-pipeline
git pull origin devin/windows-installer
.\.venv\Scripts\Activate.ps1
pip install -e .
```

---

## Сброс после ошибки

```powershell
# Полный сброс БД:
Remove-Item -Force data\state.db

# Откат одного проекта в нужный статус:
@'
import sqlite3
c = sqlite3.connect("data/state.db")
c.execute("UPDATE projects SET status='frames_ready' WHERE id=1")
c.commit()
print("ok")
'@ | Set-Content -Encoding UTF8 reset_status.py
python reset_status.py

# Очистка кеша Python (после обновления файлов):
Get-ChildItem -Recurse -Directory -Filter __pycache__ | Remove-Item -Recurse -Force
```

---

## Проверка зависимостей

```powershell
python --version        # Должен быть 3.11 или 3.12
ffmpeg -version         # Должен быть установлен
pip list | findstr video-pipeline
```
