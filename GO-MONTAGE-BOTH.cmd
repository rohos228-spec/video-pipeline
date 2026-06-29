@echo off
cd /d "%~dp0"
set PY=%~dp0.venv\Scripts\python.exe
if not exist "%PY%" set PY=python
echo === Монтаж #15 + #17 ===
"%PY%" scripts\run_montage_both.py
echo.
echo Отчёт: data\_montage_run_report.txt
pause
