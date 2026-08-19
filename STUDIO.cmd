@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Video Pipeline Studio
chcp 65001 >nul 2>&1

if "%STUDIO_HEALED%"=="1" goto :start

echo.
echo Studio: updating launcher...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/rohos228-spec/video-pipeline/main/scripts/studio.ps1' -OutFile '%~dp0scripts\studio.ps1'"
where git >nul 2>&1
if %ERRORLEVEL%==0 (
  git fetch origin main
  if %ERRORLEVEL%==0 git reset --hard origin/main
)

set STUDIO_HEALED=1
call "%~f0" %*
exit /b %ERRORLEVEL%

:start
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
