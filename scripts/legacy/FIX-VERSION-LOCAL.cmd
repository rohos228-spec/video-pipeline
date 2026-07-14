@echo off
cd /d "%~dp0"
title Fix Studio version (local, no git reset)
if not exist pyproject.toml (
    echo ERROR: run from video-pipeline folder
    pause
    exit /b 1
)
echo === Rebuild UI so badge matches server (no git reset) ===
echo.
call "%~dp0BUILD-WEB.cmd"
