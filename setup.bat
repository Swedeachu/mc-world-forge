@echo off
REM ============================================================
REM   World Forge - one-time setup
REM   Creates an isolated Python environment (.venv) and installs
REM   Flask, numpy and Pillow from requirements.txt.
REM ============================================================
setlocal
cd /d "%~dp0"

REM Pass "nopause" (used by start.bat) to skip the trailing pause.
set "DOPAUSE=pause"
if /i "%~1"=="nopause" set "DOPAUSE="

echo(
echo === World Forge setup ===
echo(

REM --- locate a Python interpreter (prefer the py launcher) -----------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY (
  where python >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo [ERROR] Python was not found on your PATH.
  echo(
  echo   Install Python 3.9 or newer from:
  echo       https://www.python.org/downloads/
  echo   During install, tick "Add Python to PATH".
  echo(
  %DOPAUSE%
  exit /b 1
)
echo Using Python launcher: %PY%

REM --- create the virtual environment ---------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment in .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Could not create the virtual environment.
    %DOPAUSE%
    exit /b 1
  )
) else (
  echo Virtual environment already exists - reusing it.
)

set "VPY=.venv\Scripts\python.exe"

REM --- install dependencies ------------------------------------------
echo Upgrading pip ...
"%VPY%" -m pip install --upgrade pip

echo Installing dependencies from requirements.txt ...
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 (
  echo(
  echo [ERROR] Installing dependencies failed - see the messages above.
  echo         Check your internet connection and try again.
  %DOPAUSE%
  exit /b 1
)

echo(
echo [OK] Setup complete. Double-click start.bat to launch World Forge.
%DOPAUSE%
exit /b 0
