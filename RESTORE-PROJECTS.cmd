@echo off
chcp 65001 >nul 2>nul
cd /d "%~dp0"
if not exist pyproject.toml (
    echo Run from video-pipeline folder
    pause
    exit /b 1
)
echo.
echo Восстановление проектов: копируем state.db в data\
echo Сначала закрой окно бэкенда.
echo.
set /p SRC="Путь к старому state.db (вставь из FIND-MY-PROJECTS): "
if not exist "%SRC%" (
    echo File not found: %SRC%
    pause
    exit /b 1
)
if not exist data mkdir data
copy /Y "%SRC%" "data\state.db"
if errorlevel 1 (
    echo Copy failed
    pause
    exit /b 1
)
echo OK copied. Start backend and refresh browser Ctrl+F5.
pause
