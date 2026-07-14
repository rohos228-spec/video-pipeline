@echo off
REM Единый лаунчер Video Pipeline Studio — двойной клик в Проводнике
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title Video Pipeline Studio
set "STUDIO_PS1=%~dp0scripts\studio.ps1"
where pwsh >nul 2>&1
if %ERRORLEVEL% equ 0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%STUDIO_PS1%" %*
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%STUDIO_PS1%" %*
)
set ERR=%ERRORLEVEL%
if %ERR% neq 0 (
    echo.
    echo Завершено с ошибкой (код %ERR%^).
    pause
)
exit /b %ERR%
