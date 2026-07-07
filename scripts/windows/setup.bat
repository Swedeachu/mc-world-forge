@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "ROOT=%%~fI"

pushd "%ROOT%" >nul
if errorlevel 1 (
    echo Failed to enter project root:
    echo %ROOT%
    exit /b 1
)

where py >nul 2>nul
if errorlevel 1 (
    set "PYTHON_EXE=python"
) else (
    set "PYTHON_EXE=py -3"
)

if not exist "%ROOT%\.venv" (
    %PYTHON_EXE% -m venv "%ROOT%\.venv"
    if errorlevel 1 (
        popd >nul
        exit /b 1
    )
)

call "%ROOT%\.venv\Scripts\activate.bat"
if errorlevel 1 (
    popd >nul
    exit /b 1
)

python -m pip install --upgrade pip
if errorlevel 1 (
    popd >nul
    exit /b 1
)

pip install -r "%ROOT%\server\requirements.txt"
if errorlevel 1 (
    popd >nul
    exit /b 1
)

call "%ROOT%\generator\scripts\windows\build_msvc.bat"
if errorlevel 1 (
    popd >nul
    exit /b 1
)

echo.
echo Setup complete. Run scripts\windows\run.bat to launch the web UI.
echo.

popd >nul
exit /b 0
