@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"
if not exist pyproject.toml (
    echo ERROR: run from video-pipeline folder
    pause
    exit /b 1
)
echo.
echo То же что ОБНОВИТЬ-ВЕРСИЮ.cmd - запускаю полное обновление...
echo.
call "%~dp0ОБНОВИТЬ-ВЕРСИЮ.cmd"
