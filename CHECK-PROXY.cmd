@echo off
setlocal
cd /d "%~dp0"
title CHECK PROXY - ElevenLabs

echo.
echo Двойной клик по этому файлу = проверка прокси из .env
echo Папка: %CD%
echo.

if not exist ".venv\Scripts\python.exe" (
    echo FAIL: нет .venv
    echo Сначала запусти install.ps1 в этой папке
    echo.
    pause
    exit /b 1
)

.venv\Scripts\python.exe scripts\check_proxy.py
set ERR=%ERRORLEVEL%

echo.
if %ERR% equ 0 (
    echo === ИТОГ: OK ===
) else (
    echo === ИТОГ: FAIL (код %ERR%) ===
    echo Для UI нажми GO.cmd и открой http://127.0.0.1:8765
)
echo.
pause
exit /b %ERR%
