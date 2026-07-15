@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === Fleet network fix: WEB_HOST=0.0.0.0 + restart ===
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\fleet-network-fix.ps1"
pause
