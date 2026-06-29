@echo off
cd /d "%~dp0"
title BACKEND :8765
set "TELEGRAM_ENABLED=false"
set "WEB_HOST=127.0.0.1"
set "WEB_PORT=8765"
echo Studio: http://127.0.0.1:8765
echo Wait: Uvicorn running on http://127.0.0.1:8765
echo.
.venv\Scripts\python.exe -m app.main
pause
