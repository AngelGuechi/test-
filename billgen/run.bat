@echo off
REM Double-click to make 100 bills, or run "run.bat 2000" to make 2000.
REM Running it again continues after the last bill in the "out" folder.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo First time setup - this takes a few minutes...
  python -m venv .venv
  if errorlevel 1 (
    echo Python was not found. Install Python 3.11 or newer from python.org, then try again.
    pause
    exit /b 1
  )
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install faker jinja2 playwright pillow numpy
  ".venv\Scripts\python.exe" -m playwright install chromium
)

set COUNT=%1
if "%COUNT%"=="" set COUNT=100

".venv\Scripts\python.exe" generate.py --count %COUNT% --out out
".venv\Scripts\python.exe" check.py --out out
echo.
echo Pictures are in: %~dp0out\images
echo Answer files are in: %~dp0out\labels
pause
