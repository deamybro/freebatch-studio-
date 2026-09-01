@echo off
rem ============================================================
rem  FreeBatch Studio - one-time setup script
rem ============================================================
setlocal
cd /d "%~dp0"

echo [FreeBatch Studio] initial setup
echo -----------------------------------

echo [1/4] checking Python...
python --version
if errorlevel 1 (
    echo ERROR: Python 3.11+ is required. Install from https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [2/4] creating virtual environment...
if not exist ".venv\Scripts\python.exe" (
    python -m venv .venv
)

echo [3/4] installing dependencies...
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
".venv\Scripts\python.exe" -m pip install --quiet -r requirements-dev.txt

echo [4/4] creating .env (copy example)...
if not exist ".env" (
    copy /Y ".env.example" ".env" >nul
    echo   Created .env - open it and add your AGNES_API_KEY.
) else (
    echo   .env already exists - leaving it untouched.
)

echo.
echo Setup complete. Run start.bat to launch FreeBatch Studio.
echo To run the tests:  .venv\Scripts\python.exe -m pytest
echo To run the linter: .venv\Scripts\ruff.exe check app tests
pause