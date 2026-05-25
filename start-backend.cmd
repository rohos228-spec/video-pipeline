@echo off
REM Start backend — keep this window open
cd /d "%~dp0"
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0run-backend.ps1"
