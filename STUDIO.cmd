@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Video Pipeline Studio
chcp 65001 >nul 2>&1

:start
if exist "%~dp0.venv\Scripts\python.exe" goto :have_venv

echo.
echo ============================================================
echo  Studio is not installed on this computer (.venv missing).
echo ============================================================
echo.
set /p "RUN_SETUP=Run quick setup now? (Y/n, Enter = Yes): "
if "%RUN_SETUP%"=="" goto :do_setup
if /i "%RUN_SETUP%"=="y" goto :do_setup
exit /b 1

:do_setup
call "%~dp0SETUP.cmd"
exit /b %ERRORLEVEL%

:have_venv

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
