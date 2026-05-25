@echo off
REM Start backend and keep window open (double-click from repo root)
cd /d "%~dp0"
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0run-backend.ps1"
pause
