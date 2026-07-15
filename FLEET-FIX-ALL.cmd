@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo === FLEET FIX ALL: hard reset main + restart + test ===
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\fleet-fix-all.ps1"
pause
