@echo off
REM Start Studio — safe to run by full path from any folder.
REM Example:
REM   "C:\Users\Love Space\Documents\video-pipeline\Open-Studio.cmd"
cd /d "%~dp0"
if not exist pyproject.toml (
    echo ERROR: This is not the video-pipeline folder.
    echo Put Open-Studio.cmd only inside your cloned repo.
    pause
    exit /b 1
)
call "%~dp0START-STUDIO.cmd"
