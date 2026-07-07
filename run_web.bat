@echo off
chcp 65001 >nul
REM Dynamically get the directory where this batch file is located
set ROOT=%~dp0
REM Remove trailing backslash
set ROOT=%ROOT:~0,-1%

cd /d %ROOT%
set PYTHONPATH=%ROOT%
set PYTHONIOENCODING=utf-8

REM Check if virtual environment exists and activate it
if exist "%ROOT%\venv\Scripts\activate.bat" (
    echo Activating virtual environment: venv
    call "%ROOT%\venv\Scripts\activate.bat"
) else if exist "%ROOT%\.venv\Scripts\activate.bat" (
    echo Activating virtual environment: .venv
    call "%ROOT%\.venv\Scripts\activate.bat"
) else (
    echo Warning: No virtual environment found, using system Python
)

start "iSDTaiM Server" cmd /k "chcp 65001 >nul && set PYTHONIOENCODING=utf-8 && cd /d %ROOT% && (if exist venv\Scripts\activate.bat (call venv\Scripts\activate.bat) else if exist .venv\Scripts\activate.bat (call .venv\Scripts\activate.bat)) && python -m uvicorn app:app --reload --host 0.0.0.0 --port 8000"

timeout /t 3 >nul
start "" http://localhost:8000
