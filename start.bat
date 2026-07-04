@echo off
REM ============================================================
REM   World Forge - launch
REM   Runs the local server and opens it in your default browser.
REM   The first run sets everything up automatically.
REM ============================================================
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo First run detected - setting things up, one moment...
  echo(
  call setup.bat nopause
  if errorlevel 1 (
    echo(
    echo Setup did not finish. Fix the problem above and try again.
    pause
    exit /b 1
  )
)

set "VPY=.venv\Scripts\python.exe"
if not exist "%VPY%" set "VPY=python"

echo(
echo ============================================================
echo   Starting World Forge at http://127.0.0.1:5000
echo   Your browser will open automatically in a few seconds.
echo(
echo   Keep this window open while you use the tool.
echo   Press Ctrl+C (or just close this window) to stop.
echo ============================================================
echo(

REM Open the browser shortly after the server starts (runs in background).
start "" /b "%~dp0open_browser.bat"

REM Run the server (blocks until you stop it).
"%VPY%" app.py

endlocal
