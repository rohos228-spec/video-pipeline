@echo off
cd /d "%~dp0"
if not exist pyproject.toml (
    echo ERROR: run from video-pipeline folder
    pause
    exit /b 1
)
echo === FIX v102 - restore web/out from git ===
echo Folder: %CD%
echo.
git fetch origin cursor/fix-launcher-update-start-977b
git checkout origin/cursor/fix-launcher-update-start-977b -- web/out web/STUDIO_VERSION
if errorlevel 1 (
    echo git checkout failed
    pause
    exit /b 1
)
echo.
echo STUDIO_VERSION:
type web\STUDIO_VERSION
echo.
findstr /C:"v10" web\out\index.html | findstr /C:"title=" | more +0
echo.
echo Now: close backend window, then UPDATE-STUDIO.cmd
echo In browser: Ctrl+F5 on http://127.0.0.1:8765
pause
