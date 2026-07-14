@echo off
REM Запуск студии — можно вызывать по полному пути из любой папки
cd /d "%~dp0"
if not exist pyproject.toml (
    echo ОШИБКА: это не папка video-pipeline.
    pause
    exit /b 1
)
call "%~dp0STUDIO.cmd" 1
