@echo off
chcp 65001 >nul
cd /d "%~dp0\.."
echo === Agent: git pull + restart ===
git fetch origin main
git reset --hard origin/main
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-backend.ps1" -Quiet -WaitSec 10 2>nul
start "" powershell.exe -NoExit -ExecutionPolicy Bypass -File "%~dp0run-backend.ps1"
echo Готово. Подожди 30 сек — hub увидит проекты.
pause
