@echo off
cd /d "%~dp0"
set ASR_BACKEND=nvidia
set NVIDIA_ASR_MODEL=nvidia/stt_ru_fastconformer_hybrid_large_pc
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\remontage.ps1" -ProjectId 15
pause
