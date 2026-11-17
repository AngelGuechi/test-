@echo off
REM Double-click to open Easy Claim AI in your web browser.
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
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

echo Starting Easy Claim AI... your browser will open at http://localhost:8501
echo Keep this window open while you use it. Close it to stop the app.
start "" cmd /c "timeout /t 5 >nul & start http://localhost:8501"
".venv\Scripts\python.exe" -m streamlit run app.py --server.headless true --server.port 8501 --browser.gatherUsageStats false
pause
