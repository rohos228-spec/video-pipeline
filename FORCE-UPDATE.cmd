@echo off
REM Жесткое обновление: git + UI + backend. Двойной клик из корня video-pipeline.
cd /d "%~dp0"
title FORCE UPDATE
if not exist pyproject.toml (
    echo ERROR: запусти из папки video-pipeline где лежит pyproject.toml
    pause
    exit /b 1
)

set "BR=fix/text-save-persistence-v153"
echo.
echo ============================================
echo   FORCE UPDATE  %CD%
echo ============================================
echo.

echo [1/5] Stop backend...
call "%~dp0stop-backend.cmd"
timeout /t 3 /nobreak >nul

echo.
echo [2/5] Git fetch + reset...
where git >nul 2>&1
if errorlevel 1 (
    echo ERROR: git not found
    pause
    exit /b 1
)
git fetch origin %BR%
if errorlevel 1 (
    echo ERROR: git fetch failed - check internet
    pause
    exit /b 1
)
git reset --hard origin/%BR%
git checkout -B %BR% origin/%BR% 2>nul
echo HEAD: 
git rev-parse --short HEAD

echo.
echo [3/5] Restore web/out (delete stale UI first)...
if exist web\out rmdir /s /q web\out
git checkout origin/%BR% -- web/out web/STUDIO_VERSION
echo STUDIO_VERSION:
type web\STUDIO_VERSION

echo.
echo [4/5] Python deps...
if not exist .venv\Scripts\python.exe (
    echo ERROR: .venv missing - run install.ps1 once
    pause
    exit /b 1
)
.venv\Scripts\python.exe -m pip install -e . -q

echo.
echo [5/5] Start backend...
start "video-pipeline backend" powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-backend.ps1"
echo Wait for Uvicorn running on http://127.0.0.1:8765
timeout /t 8 /nobreak >nul
start http://127.0.0.1:8765/

echo.
echo DONE. In browser press Ctrl+F5
echo Check version bottom-left: should match STUDIO_VERSION above
echo.
pause
