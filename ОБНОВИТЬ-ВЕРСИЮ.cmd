@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"
title Обновить версию Studio
if not exist pyproject.toml (
    echo.
    echo ОШИБКА: открой папку video-pipeline в Документах
    echo   C:\Users\%USERNAME%\Documents\video-pipeline
    echo.
    pause
    exit /b 1
)
echo.
echo ========================================
echo   ОБНОВЛЕНИЕ ВЕРСИИ STUDIO
echo   Папка: %CD%
echo ========================================
echo.
echo 1) Скачает новую версию с GitHub
echo 2) Подставит UI (web/out) - сейчас v109+
echo 3) Перезапустит бэкенд
echo.
echo НЕ ЗАКРЫВАЙ окно "video-pipeline backend" после старта!
echo.
pause
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Update-Studio.ps1"
set ERR=%ERRORLEVEL%
echo.
if %ERR%==0 (
    echo Готово. Браузер: http://127.0.0.1:8765  Ctrl+F5
    start http://127.0.0.1:8765/?studio_refresh=1
) else (
    echo Не вышло. Прочитай красные строки выше.
    echo Если backend - открой окно run-backend и пришли текст ошибки.
)
echo.
pause
exit /b %ERR%
