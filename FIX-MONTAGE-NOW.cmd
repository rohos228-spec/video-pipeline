@echo off
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0FIX-MONTAGE-NOW.ps1" %*
pause
