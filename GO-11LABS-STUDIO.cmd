@echo off
cd /d "%~dp0"
title Rebuild + restart Studio (11Labs)
echo === 1. Stop :8765 ===
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"
timeout /t 2 /nobreak >nul

echo === 2. Build web (11Labs tab) ===
cd web
call npm run build
if errorlevel 1 (
  echo BUILD FAILED
  cd ..
  pause
  exit /b 1
)
cd ..

echo === 3. Start backend (отдельное окно — НЕ ЗАКРЫВАТЬ) ===
start "video-pipeline backend" cmd /k "cd /d %~dp0 && title BACKEND :8765 && set TELEGRAM_ENABLED=false && set WEB_HOST=127.0.0.1 && set WEB_PORT=8765 && echo Waiting for Uvicorn on http://127.0.0.1:8765 ... && .venv\Scripts\python.exe -m app.main"

echo === 4. Wait 20 sec, check port ===
timeout /t 20 /nobreak >nul
powershell -NoProfile -Command "$ok = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue; if ($ok) { Write-Host 'BACKEND OK on :8765' -ForegroundColor Green; exit 0 } else { Write-Host 'BACKEND NOT RUNNING — смотри окно video-pipeline backend' -ForegroundColor Red; exit 1 }"
if errorlevel 1 (
  echo.
  echo Backend не поднялся. Запусти вручную в этом окне:
  echo   set TELEGRAM_ENABLED=false
  echo   set WEB_HOST=127.0.0.1
  echo   .venv\Scripts\python.exe -m app.main
  pause
  exit /b 1
)
start http://127.0.0.1:8765
echo.
echo OK — открой http://127.0.0.1:8765 и нажми Ctrl+F5
echo Вкладка 11Labs — в topbar между Сеть и Промты
pause
