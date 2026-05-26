@echo off
cd /d "%~dp0"
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0scripts\stop-backend.ps1"
if errorlevel 1 echo stop-backend had errors
pause
