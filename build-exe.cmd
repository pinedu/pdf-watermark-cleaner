@echo off
setlocal
set "PYTHON=C:\Users\Administrator\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
set "ROOT=%~dp0"
"%PYTHON%" -m PyInstaller --noconfirm --clean --onefile --name "PDFWatermarkCleaner" --add-data "%ROOT%templates;templates" --add-data "%ROOT%static;static" --collect-all pymupdf --collect-all PIL "%ROOT%app.py"
if errorlevel 1 (
  echo Build failed.
  pause
  exit /b 1
)
echo.
echo EXE created at: %ROOT%dist\PDFWatermarkCleaner.exe
endlocal
