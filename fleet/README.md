# Fleet: второй ПК без ручной возни

## На hub (главный ПК) — один раз после настройки

1. Установи Tailscale и войди в **тот же аккаунт**, что будешь использовать на воркерах.
2. Создай **Auth key** (для воркеров без регистрации в браузере):  
   https://login.tailscale.com/admin/settings/keys → Generate auth key → **Reusable**.
3. Выполни:

```powershell
cd $env:USERPROFILE\Desktop\video-pipeline
powershell -ExecutionPolicy Bypass -File .\scripts\export-fleet-manifest.ps1 -Push
```

4. Открой `fleet/secrets.env`, вставь `TAILSCALE_AUTH_KEY=tskey-auth-...`
5. Если репозиторий **приватный**, можно запушить секреты (удобно для Cursor на втором ПК):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\export-fleet-manifest.ps1 -PushSecrets
```

Для **публичного** GitHub — `secrets.env` не коммить; скопируй файл на флешку/USB в `fleet/secrets.env` на воркере.

## На worker (второй ПК)

1. Клонируй репо (или открой в Cursor).
2. Скажи агенту: **«настрой fleet worker»** — или вручную:

```powershell
cd $env:USERPROFILE\Desktop\video-pipeline
git pull
powershell -ExecutionPolicy Bypass -File .\scripts\bootstrap-fleet-worker.ps1 -StartStudio
```

Скрипт: Tailscale (auth key) → `.env` → сборка UI → Studio agent.

## Файлы

| Файл | В git | Содержимое |
|------|-------|------------|
| `fleet/manifest.json` | да | IP hub, URL Studio, defaults |
| `fleet/secrets.env` | только private / вручную | токены, пароль, Tailscale auth key |
| `.env` | нет | локальная конфигурация ПК |
