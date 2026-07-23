@echo off
REM Обновить + запустить Studio (один двойной клик)
cd /d "%~dp0"
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0update-and-start.ps1" %*
if errorlevel 1 pause
