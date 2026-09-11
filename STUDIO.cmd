@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Video Pipeline Studio
chcp 65001 >nul 2>&1

:start
if exist "%~dp0.venv\Scripts\python.exe" goto :have_venv

echo.
echo ============================================================
echo  Студия еще не установлена на этом компьютере (.venv не найден).
echo ============================================================
echo.
set /p "RUN_SETUP=Запустить быструю установку прямо сейчас? (Y/n, Enter = Да): "
if "%RUN_SETUP%"=="" goto :do_setup
if /i "%RUN_SETUP%"=="y" goto :do_setup
if /i "%RUN_SETUP%"=="д" goto :do_setup
exit /b 1

:do_setup
call "%~dp0SETUP.cmd"
exit /b %ERRORLEVEL%

:have_venv

set "STUDIO_PS1=%~dp0scripts\studio.ps1"
set "VP_REPO_ROOT=%~dp0"
if "%VP_REPO_ROOT:~-1%"=="\" set "VP_REPO_ROOT=%VP_REPO_ROOT:~0,-1%"

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
