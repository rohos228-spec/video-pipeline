@echo off
cd /d "%~dp0"
title Build + Start Studio
echo.
echo === 1/2 npm run build ===
cd web
call npm run build
if errorlevel 1 (
    echo BUILD FAILED
    cd ..
    pause
    exit /b 1
)
cd ..
echo.
echo === 2/2 Backend :8765 (отдельное окно, НЕ ЗАКРЫВАТЬ) ===
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
start "BACKEND :8765 — не закрывать" cmd /k "cd /d %~dp0 && title BACKEND :8765 && set TELEGRAM_ENABLED=false && set WEB_HOST=127.0.0.1 && set WEB_PORT=8765 && echo Studio: http://127.0.0.1:8765 && echo Жди: Uvicorn running on http://127.0.0.1:8765 && .venv\Scripts\python.exe -m app.main"
timeout /t 5 /nobreak >nul
start http://127.0.0.1:8765
echo.
echo OK — браузер откроется. Если пусто: подожди Uvicorn в окне BACKEND, потом Ctrl+F5
echo Вкладка 11Labs — между Сеть и Промты
pause
