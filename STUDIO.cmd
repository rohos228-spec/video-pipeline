@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Video Pipeline Studio
chcp 65001 >nul 2>&1

:start
set "STUDIO_PS1=%~dp0scripts\studio.ps1"
set "VP_REPO_ROOT=%~dp0"
if "%VP_REPO_ROOT:~-1%"=="\" set "VP_REPO_ROOT=%VP_REPO_ROOT:~0,-1%"

REM STUDIO_HEALED: UTF-8 BOM on studio.ps1 so Windows PowerShell 5.1 can parse Cyrillic.
if exist "%~dp0scripts\heal-studio-ps1.ps1" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\heal-studio-ps1.ps1" -Path "%STUDIO_PS1%"
)

where pwsh >nul 2>&1
if %ERRORLEVEL% equ 0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%STUDIO_PS1%" %*
) else (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%STUDIO_PS1%" %*
)
set ERR=%ERRORLEVEL%
if %ERR% neq 0 (
    echo.
    echo Error code %ERR%.
    pause
)
exit /b %ERR%
