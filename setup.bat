@echo off
REM One-time setup: creates a private Python environment and installs everything.
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)
echo Creating environment...
python -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
if errorlevel 1 (
  echo.
  echo Something went wrong installing packages. See the messages above.
  pause
  exit /b 1
)
echo.
echo Setup complete! Double-click run.bat to start the app.
pause
