@echo off
setlocal
chcp 65001 >nul 2>&1
cd /d "%~dp0.."

REM A15: before the archive is built, make consistent, verified copies of state.db and tracker.db
REM (sqlite online backup; a plain file copy of a running WAL database can be torn).
set "VP_PY=%CD%\.venv\Scripts\python.exe"
if not exist "%VP_PY%" set "VP_PY=python"
"%VP_PY%" "%~dp0backup_db.py" backup --tag export
if errorlevel 1 goto backup_failed

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0export-full-backup.ps1" %*
pause
exit /b 0

:backup_failed
echo.
echo ОШИБКА: копия баз не создана или не прошла проверку. Архив НЕ собран, чтобы не выдать битую копию за целую.
echo Подробности выше. Исправьте причину и запустите снова.
pause
exit /b 1
