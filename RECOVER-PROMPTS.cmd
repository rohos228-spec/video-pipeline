@echo off
REM Вернуть промты из git stash после STUDIO [4] (если пропали).
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title Recover prompts from stash
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
echo === Recover prompts/ from Studio git stash ===
echo.
"%PY%" scripts\return_prompts_from_stash.py --repo "%CD%" --all-studio --json
echo.
echo Если файлы вернулись — открой Studio и проверь список промтов.
echo Stash list: git stash list
echo.
pause
