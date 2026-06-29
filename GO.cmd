@echo off
setlocal
cd /d "%~dp0"
title GO Studio

if not exist ".venv\Scripts\python.exe" (
    echo FAIL: no .venv - run install.ps1 first
    pause
    exit /b 1
)

echo.
echo === 1/5 preflight python ===
.venv\Scripts\python.exe -c "from app.web.api import create_app; create_app(); print('create_app OK')"
if errorlevel 1 (
    echo FAIL: python import error - fix code above
    pause
    exit /b 1
)

echo.
echo === 2/5 stop old :8765 ===
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }"

echo.
echo === 3/5 npm run build ===
cd web
call npm run build
if errorlevel 1 (
    echo BUILD FAILED
    cd ..
    pause
    exit /b 1
)
cd ..

if not exist "web\out\index.html" (
    echo FAIL: web\out\index.html missing
    pause
    exit /b 1
)

echo.
echo === 4/5 start backend (window BACKEND :8765 - DO NOT CLOSE) ===
start "BACKEND :8765" cmd /k "%~dp0start-backend.cmd"

echo.
echo === 5/5 wait for /api/health (60 sec) ===
powershell -NoProfile -Command "for($i=0;$i -lt 30;$i++){ Start-Sleep -Seconds 2; try { $h=Invoke-RestMethod 'http://127.0.0.1:8765/api/health' -TimeoutSec 3; if($h.status -eq 'ok'){ exit 0 } } catch {} }; exit 1"
if errorlevel 1 (
    echo.
    echo FAIL: backend not responding on :8765
    echo Open window BACKEND :8765 and read Python error
    echo Or run: .venv\Scripts\python.exe -m app.main
    pause
    exit /b 1
)

start http://127.0.0.1:8765
echo.
echo OK - http://127.0.0.1:8765
echo Press Ctrl+F5 in browser. Tab 11Labs in topbar.
pause
