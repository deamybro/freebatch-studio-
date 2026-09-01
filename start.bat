@echo off
rem ============================================================
rem  FreeBatch Studio - Windows start script
rem  Checks Python, creates .venv if needed, installs deps,
rem  starts the app and opens the browser.
rem ============================================================
setlocal
cd /d "%~dp0"

echo [FreeBatch Studio] checking Python...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python 3.11+ is required but not found on PATH.
    echo Install it from https://www.python.org/downloads/ and re-run.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [FreeBatch Studio] creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: could not create .venv
        pause
        exit /b 1
    )
)

echo [FreeBatch Studio] ensuring dependencies...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo ERROR: dependency installation failed. Check requirements.txt.
    pause
    exit /b 1
)

if not exist ".env" if exist ".env.example" (
    echo [FreeBatch Studio] creating .env from .env.example (add your AGNES_API_KEY)
    copy /Y ".env.example" ".env" >nul
)

echo [FreeBatch Studio] starting...
".venv\Scripts\python.exe" run.py
pause