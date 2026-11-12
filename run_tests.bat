@echo off
REM Runs the automatic tests (fake broker + fake AI, no money involved).
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m pytest -v
pause
