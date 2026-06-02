# Studio Guardian

Автопроверки веб-студии **без** Outsee/ElevenLabs.

## Быстрый аудит (API)

Бэкенд должен слушать `:8765`:

Из **корня** `video-pipeline` (не из `C:\Users\Love Space`):

```cmd
STUDIO-AUDIT.cmd
```

или PowerShell:

```powershell
cd "C:\Users\Love Space\video-pipeline"
powershell -ExecutionPolicy Bypass -File .\scripts\guardian\run-studio-audit.ps1
```

## API + Playwright (экран)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\guardian\run-studio-audit.ps1 -E2E
```

Первый раз:

```powershell
cd web
npm install
npx playwright install chromium
```

## Что ловит

- `/api/health`, список проектов, `studio-version`
- `dry_run` для plan / запрет для video
- регрессия: `frames_ready` при 3+ видео на диске (#15)
- e2e: загрузка UI, выбор проекта, V-menu → кнопка Run активна

Логи бэкенда: `data\backend.log`, сессия: `data\backend-<pid>.log`
