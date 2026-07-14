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
echo OK. Restart backend, open http://127.0.0.1:8765, Ctrl+F5
echo Badge: same v number as above, gray (no yellow !)
pause
