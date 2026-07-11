@echo off
REM Восстановить только UI (web/out) с GitHub — кнопки Материалы/Сеть/Кадры.
cd /d "%~dp0"
title RESTORE UI
if not exist pyproject.toml (
    echo ERROR: run from video-pipeline root
    pause
    exit /b 1
)

set "BR=devin/windows-installer"
echo.
echo === RESTORE UI from origin/%BR% ===
echo.

call "%~dp0stop-backend.cmd" 2>nul
timeout /t 2 /nobreak >nul

git fetch origin %BR%
if errorlevel 1 (
    echo ERROR: git fetch failed
    pause
    exit /b 1
)

git checkout %BR% 2>nul
git reset --hard origin/%BR%

if exist web\out rmdir /s /q web\out
git checkout origin/%BR% -- web/out web/STUDIO_VERSION scripts/Pull-Hotfix-Safe.ps1 PULL-HOTFIX.cmd

echo.
echo STUDIO_VERSION:
type web\STUDIO_VERSION
echo.

findstr /C:"Сеть" web\out\_next\static\chunks\app\page-*.js >nul
if errorlevel 1 (
    echo ERROR: web/out still missing Сеть button - git checkout failed
    pause
    exit /b 1
)
echo OK: web/out contains Сеть button

echo.
echo Start Studio, then Ctrl+F5 in browser
start "video-pipeline" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-backend.ps1"
timeout /t 6 /nobreak >nul
start http://127.0.0.1:8765/
pause
