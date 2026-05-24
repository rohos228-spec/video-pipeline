@echo off
REM Create Desktop shortcut (double-click once)
cd /d "%~dp0"
powershell.exe -ExecutionPolicy Bypass -NoProfile -File "%~dp0create-desktop-shortcut.ps1"
pause
