@echo off
REM Optional: builds Eimeria.exe yourself (normally GitHub builds it for every release).
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m pytest -q
if errorlevel 1 (
  echo Tests failed, not building.
  pause
  exit /b 1
)
python -m PyInstaller --noconfirm --clean --windowed --name Eimeria --collect-data tzdata --collect-submodules alpaca run_app.py
dist\Eimeria\Eimeria.exe --smoke-test "%TEMP%\eimeria_smoke.txt"
type "%TEMP%\eimeria_smoke.txt"
echo.
echo Done. Your app is in dist\Eimeria\Eimeria.exe (copy the whole dist\Eimeria folder).
pause
