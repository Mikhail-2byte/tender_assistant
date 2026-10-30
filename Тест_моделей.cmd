@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
"%~dp0.venv\Scripts\python.exe" -m scripts.ai_benchmark --live --protocol text-json
echo.
pause
