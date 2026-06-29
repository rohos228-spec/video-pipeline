@echo off
cd /d "%~dp0"
title ElevenLabs — получить API key (1 раз, через VPN)
echo.
echo === ElevenLabs: логин и API key ===
echo.
echo 1. Включи VPN на USA или EU ^(не бесплатный datacenter^)
echo 2. Откроется Chrome профиля pipeline — войди вручную ОДИН раз
echo 3. Profile - API Keys - Create - Copy sk_...
echo 4. Вставь в .env:  ELEVENLABS_API_KEY=sk_...
echo 5. Перезапусти Studio — TTS пойдет через API, браузер не нужен
echo.
pause
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Start-ChromeCDP.ps1" -OpenUrl "https://elevenlabs.io/app/settings/api-keys"
pause
