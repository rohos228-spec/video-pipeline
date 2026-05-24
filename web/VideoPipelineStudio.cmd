@echo off
REM Запуск из web\ — переход в корень репозитория
cd /d "%~dp0.."
call "%~dp0..\VideoPipelineStudio.cmd"
