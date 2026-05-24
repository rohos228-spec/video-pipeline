@echo off
REM Quick check: is the local repo up to date and UI built?
cd /d "%~dp0"
echo === Video Pipeline update check ===
echo Folder: %CD%
echo.

git rev-parse --abbrev-ref HEAD 2>nul
if errorlevel 1 (
    echo [ERROR] Not a git repo
    goto end
)

echo Branch: 
git rev-parse --abbrev-ref HEAD
echo Local commit:
git rev-parse --short HEAD
echo Remote devin/windows-installer:
git rev-parse --short origin/devin/windows-installer 2>nul
if errorlevel 1 (
    echo   run: git fetch origin
) else (
    git fetch origin -q 2>nul
    git rev-parse --short origin/devin/windows-installer
)

echo.
if exist web\STUDIO_VERSION (
    echo STUDIO_VERSION:
    type web\STUDIO_VERSION
) else (
    echo [WARN] web\STUDIO_VERSION missing
)

echo.
if exist web\out\index.html (
    echo web\out\index.html: OK
    for %%F in (web\out\index.html) do echo   modified: %%~tF
) else (
    echo [WARN] web\out\index.html missing - run button 6 Build Web UI
)

echo.
echo Open in browser after Start Studio:
echo   http://127.0.0.1:8765/api/studio-version
echo   http://127.0.0.1:8765  (Ctrl+F5 hard refresh)
echo.
echo If local commit != remote: run Update all or:
echo   git checkout devin/windows-installer
echo   git pull origin devin/windows-installer
echo   cd web ^&^& npm install ^&^& npm run build

:end
pause
