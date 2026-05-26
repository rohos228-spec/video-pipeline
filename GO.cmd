@echo off
setlocal
title Video Pipeline GO
echo.
echo === Video Pipeline: update + start ===
echo.

set "BR=cursor/fix-launcher-update-start-977b"
set "URL=https://github.com/rohos228-spec/video-pipeline.git"
set "REPO1=%USERPROFILE%\Documents\video-pipeline"
set "REPO2=%USERPROFILE%\video-pipeline"
set "REPO3=%~dp0"
set "REPO="

if exist "%REPO1%\pyproject.toml" set "REPO=%REPO1%"
if not defined REPO if exist "%REPO2%\pyproject.toml" set "REPO=%REPO2%"
if not defined REPO if exist "%REPO3%pyproject.toml" set "REPO=%REPO3%"

if not defined REPO (
    set "REPO=%REPO1%"
    echo No project found. Will clone to:
    echo   %REPO%
    echo.
)

if not exist "%REPO%" mkdir "%REPO%"
cd /d "%REPO%"

echo Folder:
echo   %CD%
echo.

where git >nul 2>&1
if errorlevel 1 (
    echo ERROR: install Git from https://git-scm.com/download/win
    pause
    exit /b 1
)

if not exist ".git" (
    if exist "pyproject.toml" (
        echo ERROR: this folder is not a git clone. Move it aside or delete, then run GO.cmd again.
        pause
        exit /b 1
    )
    echo Cloning...
    git clone -b %BR% %URL% .
    if errorlevel 1 (
        echo Clone failed - check internet
        pause
        exit /b 1
    )
)

echo Updating (%BR%)...
git fetch origin %BR%
git checkout -B %BR% origin/%BR%
git reset --hard origin/%BR%
git checkout origin/%BR% -- web/out web/STUDIO_VERSION 2>nul
echo.
type web\STUDIO_VERSION 2>nul
echo.

if exist "UPDATE-STUDIO.cmd" (
    call "UPDATE-STUDIO.cmd"
    exit /b %ERRORLEVEL%
)
if exist "start-backend.cmd" (
    call "start-backend.cmd"
    exit /b %ERRORLEVEL%
)

echo ERROR: files missing after git update
pause
exit /b 1
