@echo off
set "PYTHON=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PYTHON%" (
  echo Python environment not found.
  pause
  exit /b 1
)
start "" http://127.0.0.1:8765
"%PYTHON%" "%~dp0app.py"
pause
