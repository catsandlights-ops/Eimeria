@echo off
REM Optional: builds a standalone Eimeria.exe in the dist\Eimeria folder.
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m pytest -q
if errorlevel 1 (
  echo Tests failed, not building.
  pause
  exit /b 1
)
pyinstaller --noconfirm --windowed --name Eimeria run_app.py
echo.
echo Done. Your app is in dist\Eimeria\Eimeria.exe
pause
