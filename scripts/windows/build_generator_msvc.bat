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

call "%ROOT%\generator\scripts\windows\build_msvc.bat"
set "STATUS=%ERRORLEVEL%"

popd >nul
exit /b %STATUS%
