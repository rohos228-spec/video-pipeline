@echo off
cd /d "%~dp0"
title Restore Chrome profile
echo.
echo Перенос логинов ChatGPT/outsee с другого компа.
echo.
echo Пример (Love Space -^> Ai Creator):
echo   C:\Users\Love Space\.vp_browser_data
echo.
set /p FROM="Путь к папке .vp_browser_data на старом ПК: "
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\Restore-Chrome-Profile.ps1" -FromPath "%FROM%"
pause
