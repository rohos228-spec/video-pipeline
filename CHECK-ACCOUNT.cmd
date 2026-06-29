@echo off
setlocal
cd /d "%~dp0"
title CHECK ElevenLabs ACCOUNT

echo.
echo Какой тариф видит API key из .env (не то что на сайте в другом логине!)
echo.

if not exist ".venv\Scripts\python.exe" (
    echo FAIL: no .venv
    pause
    exit /b 1
)

.venv\Scripts\python.exe scripts\diag_elevenlabs_account.py
set ERR=%ERRORLEVEL%
echo.
echo Browser: https://elevenlabs.io/app/voice-library  ^(Add voice - Instant Voice Clone^)
echo         https://elevenlabs.io/app/subscription
echo         https://elevenlabs.io/app/settings/api-keys
echo.
echo API:     http://127.0.0.1:8765/api/elevenlabs/account-diag
echo.
pause
exit /b %ERR%
