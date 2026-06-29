@echo off
cd /d "%~dp0"
title Build Studio UI
if not exist web\package.json (
    echo ERROR: web\package.json not found
    pause
    exit /b 1
)
if not exist web\STUDIO_VERSION (
    echo ERROR: web\STUDIO_VERSION missing
    pause
    exit /b 1
)
echo === STUDIO_VERSION before build ===
type web\STUDIO_VERSION
echo.
echo === npm run build (web/) ===
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
echo === BUILD OK — это НЕ запуск программы ===
echo UI лежит в web\out\ — его отдаёт Python-бэкенд на :8765
echo.
choice /C YN /M "Запустить backend сейчас"
if errorlevel 2 goto skip_start
if errorlevel 1 goto do_start
:do_start
start "BACKEND :8765 — не закрывать" cmd /k "cd /d %~dp0 && title BACKEND :8765 && set TELEGRAM_ENABLED=false && set WEB_HOST=127.0.0.1 && set WEB_PORT=8765 && echo Studio: http://127.0.0.1:8765 && .venv\Scripts\python.exe -m app.main"
timeout /t 4 /nobreak >nul
start http://127.0.0.1:8765
echo Открыл http://127.0.0.1:8765 — жди Uvicorn в окне BACKEND, потом Ctrl+F5
goto end
:skip_start
echo Запуск вручную:  start-backend.cmd   или   BUILD-AND-START.cmd
:end
echo Badge: same v number as above, gray (no yellow !)
pause
