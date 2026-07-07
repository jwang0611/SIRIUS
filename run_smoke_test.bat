@echo off
chcp 65001 >nul 2>&1
echo ================================================
echo   iSDTaiM Smoke Test
echo ================================================
echo.

REM Activate venv if exists
if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
)

REM Run with all arguments passed through
python tests/smoke_test.py %*

pause
