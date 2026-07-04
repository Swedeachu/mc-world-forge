@echo off
REM Start the World Forge web UI.
cd /d "%~dp0"
echo Installing dependencies (first run only)...
py -m pip install -r requirements.txt
echo Starting World Forge on http://127.0.0.1:5000
py app.py
pause
