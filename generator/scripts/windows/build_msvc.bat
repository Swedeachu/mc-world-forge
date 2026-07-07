@echo off
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "GENERATOR_ROOT=%%~fI"

pushd "%GENERATOR_ROOT%" >nul
if errorlevel 1 (
    echo Failed to enter generator root:
    echo %GENERATOR_ROOT%
    exit /b 1
)

where cmake >nul 2>nul
if errorlevel 1 (
    echo CMake was not found in PATH.
    echo Run your CMake installer script, then open a new cmd window and try again.
    popd >nul
    exit /b 1
)

where cl >nul 2>nul
if errorlevel 1 (
    set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
    if exist "%VSWHERE%" (
        for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSINSTALL=%%i"
        if defined VSINSTALL (
            call "%VSINSTALL%\VC\Auxiliary\Build\vcvars64.bat"
            if errorlevel 1 (
                popd >nul
                exit /b 1
            )
        )
    )
)

where cl >nul 2>nul
if errorlevel 1 (
    echo MSVC cl.exe was not found.
    echo Install Visual Studio 2022 Build Tools with the Desktop development with C++ workload.
    popd >nul
    exit /b 1
)

if not exist "%GENERATOR_ROOT%\build\msvc" mkdir "%GENERATOR_ROOT%\build\msvc"

cmake -S "%GENERATOR_ROOT%" -B "%GENERATOR_ROOT%\build\msvc" -G "Visual Studio 17 2022" -A x64
if errorlevel 1 (
    popd >nul
    exit /b 1
)

cmake --build "%GENERATOR_ROOT%\build\msvc" --config Release --parallel
if errorlevel 1 (
    popd >nul
    exit /b 1
)

echo.
echo Built native generator to "%GENERATOR_ROOT%\bin".
echo.

popd >nul
exit /b 0
