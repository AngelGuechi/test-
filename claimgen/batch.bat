@echo off
REM Make draft claims for every bill in a folder.
REM Usage:  batch.bat "C:\path\to\billgen\out\labels"
cd /d "%~dp0"
if "%~1"=="" (
  echo Usage: batch.bat "C:\path\to\billgen\out\labels"
  pause
  exit /b 1
)
".venv\Scripts\python.exe" batch.py --bills "%~1" --out claims\drafts
pause
