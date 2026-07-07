@echo off
setlocal EnableExtensions

call "%~dp0start_web.bat"
set "STATUS=%ERRORLEVEL%"

if not "%STATUS%"=="0" (
    echo.
    echo MC World Forge failed to start. Exit code: %STATUS%
    echo.
    pause
    exit /b %STATUS%
)

exit /b 0
