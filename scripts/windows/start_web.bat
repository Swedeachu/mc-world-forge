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

if not exist "%ROOT%\.venv\Scripts\activate.bat" (
    echo Virtual environment was not found.
    echo Run scripts\windows\setup.bat first.
    popd >nul
    exit /b 1
)

if not exist "%ROOT%\generator\bin\mc_world_forge_generator.exe" (
    echo Native generator binary was not found. Building it now...
    call "%ROOT%\generator\scripts\windows\build_msvc.bat"
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

echo.
echo MC World Forge project root:
echo %ROOT%
echo.
echo Starting web UI at http://127.0.0.1:5000
echo.

py -m server.app
set "STATUS=%ERRORLEVEL%"

popd >nul
exit /b %STATUS%
