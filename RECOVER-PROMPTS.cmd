@echo off
REM Вернуть промты после STUDIO [4], если пропали (aside + git stash).
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title Recover prompts
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if "%PY%"=="" if exist ".venv\bin\python" set "PY=.venv\bin\python"
if "%PY%"=="" set "PY=python"
echo === Recover prompts/ (aside backup + Studio stash) ===
echo.
"%PY%" scripts\return_prompts_from_stash.py --repo "%CD%" --startup-once --json
echo.
echo Если файлы вернулись — открой Studio (Ctrl+F5) и проверь список промтов.
echo Stash: git stash list
echo Aside: %%LOCALAPPDATA%%\video-pipeline\prompts_aside_*
echo.
pause
