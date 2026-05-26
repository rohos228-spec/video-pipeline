@echo off
REM Пересборка web/out — если в Studio застряла v102, а в git уже v105+
cd /d "%~dp0"

echo === force-rebuild-web ===
echo STUDIO_VERSION:
type web\STUDIO_VERSION 2>nul
echo.

git fetch origin cursor/fix-launcher-update-start-977b 2>nul
git pull --ff-only origin cursor/fix-launcher-update-start-977b 2>nul
if errorlevel 1 (
    echo [WARN] git pull failed — building LOCAL files anyway
)

where npm >nul 2>&1
if errorlevel 1 (
    echo [ERROR] npm not found. Install Node.js LTS, reopen terminal.
    pause
    exit /b 1
)

cd web
call npm install
if errorlevel 1 goto fail
call npm run build
if errorlevel 1 goto fail
cd ..

if not exist web\out\index.html (
    echo [ERROR] web\out\index.html missing after build
    goto fail
)

echo.
echo Built UI label in web/out:
findstr /R "v[0-9][0-9]*" web\out\index.html | findstr /C:"v" | more +0
echo.
echo Restart: VideoPipelineStudio.cmd -^> * Update + Start
pause
exit /b 0

:fail
echo [FAIL] npm build failed
pause
exit /b 1
