@echo off
REM Только папка, где лежит этот файл. Никакого clone в Documents.
cd /d "%~dp0"
title Video Pipeline
if not exist pyproject.toml (
    echo.
    echo ERROR: GO.cmd must be inside YOUR video-pipeline folder.
    echo This file: %~f0
    echo.
    pause
    exit /b 1
)
call "%~dp0UPDATE-STUDIO.cmd"
exit /b %ERRORLEVEL%
