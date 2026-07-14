# Старые скрипты запуска и обновления (архив для отката)

**Актуальный лаунчер:** `STUDIO.cmd` в корне → `scripts/studio.ps1` (меню 1–6).

Не удаляйте — можно откатиться вручную.

## Из корня (перенесено)

| Файл | Было | Замена в STUDIO.cmd |
|------|------|---------------------|
| BACKEND.cmd | Запуск бэкенда | **[1]** Запустить студию |
| BROWSER-OUTSEE.cmd / .ps1 | Chrome + outsee | **[3]** Браузер с ИИ |
| Diagnose-Chrome.cmd | Диагностика Chrome | **[6]** Диагностика |
| OBNOVIT-I-ZAPUSK.cmd / Obnovit-i-Zapusk.ps1 | Обновить и запустить | **[4]** Обновить и запустить |
| PULL-HOTFIX.cmd | Hotfix pull | **[4]** |
| PUSH-V160.cmd / FIX-V160.cmd | Версия v160 | git вручную |
| FIX-VERSION-LOCAL.cmd | Локальный фикс версии | **[5]** / **[6]** |
| FIND-MY-PROJECTS.cmd | Поиск проектов | Studio UI |
| START-STUDIO.cmd, Open-Studio.cmd | Старый запуск | **[1]** |
| stop-backend.cmd | Стоп бэкенда | **[2]** Остановить всё |

## Ручной запуск (разработка)

- Бэкенд: `scripts\run-backend.ps1`
- Chrome CDP: `scripts\Start-ChromeCDP.ps1`
- Стоп бэкенда: `scripts\stop-backend.ps1`
