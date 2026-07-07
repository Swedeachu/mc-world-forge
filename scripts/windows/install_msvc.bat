@echo off
setlocal EnableExtensions EnableDelayedExpansion

title Install MSVC Build Tools

echo.
echo ==========================================
echo Installing Visual Studio 2022 Build Tools
echo Desktop development with C++
echo ==========================================
echo.

where cl >nul 2>nul
if %errorlevel%==0 (
	echo MSVC cl.exe is already visible in this terminal.
	cl
	echo.
	pause
	exit /b 0
)

set "DOWNLOAD_DIR=%TEMP%\vs_buildtools_install"
set "INSTALLER=%DOWNLOAD_DIR%\vs_BuildTools.exe"
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"

if not exist "%DOWNLOAD_DIR%" (
	mkdir "%DOWNLOAD_DIR%"
)

echo Downloading Visual Studio Build Tools bootstrapper...
echo.

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
	"[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12;" ^
	"Invoke-WebRequest -Uri 'https://aka.ms/vs/17/release/vs_BuildTools.exe' -OutFile '%INSTALLER%' -UseBasicParsing"

if not exist "%INSTALLER%" (
	echo.
	echo ERROR: Failed to download vs_BuildTools.exe.
	echo Check your internet connection and try again.
	echo.
	pause
	exit /b 1
)

echo.
echo Installing MSVC C++ Build Tools...
echo.

"%INSTALLER%" ^
	--quiet ^
	--wait ^
	--norestart ^
	--nocache ^
	--add Microsoft.VisualStudio.Workload.VCTools ^
	--includeRecommended

set "INSTALL_EXIT=%errorlevel%"

if "%INSTALL_EXIT%"=="3010" (
	echo.
	echo Visual Studio Build Tools installed, but Windows wants a restart.
	echo Restart Windows before building if cl.exe is still not found.
	echo.
) else (
	if not "%INSTALL_EXIT%"=="0" (
		echo.
		echo ERROR: Visual Studio Build Tools installer failed.
		echo Installer exit code: %INSTALL_EXIT%
		echo.
		pause
		exit /b %INSTALL_EXIT%
	)
)

echo.
echo Searching for Visual Studio installation...
echo.

if not exist "%VSWHERE%" (
	echo ERROR: vswhere.exe was not found.
	echo Expected:
	echo %VSWHERE%
	echo.
	echo The Visual Studio installer may not have completed correctly.
	echo.
	pause
	exit /b 1
)

for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do (
	set "VSINSTALL=%%i"
)

if not defined VSINSTALL (
	echo ERROR: Visual Studio Build Tools installed, but VC tools were not found.
	echo Open Visual Studio Installer and make sure "Desktop development with C++" is installed.
	echo.
	pause
	exit /b 1
)

set "VCVARS=%VSINSTALL%\VC\Auxiliary\Build\vcvars64.bat"

if not exist "%VCVARS%" (
	echo ERROR: vcvars64.bat was not found.
	echo Expected:
	echo %VCVARS%
	echo.
	pause
	exit /b 1
)

echo Found Visual Studio:
echo %VSINSTALL%
echo.

echo Loading MSVC x64 environment...
call "%VCVARS%"

where cl >nul 2>nul
if not %errorlevel%==0 (
	echo.
	echo ERROR: MSVC was installed, but cl.exe is still not visible.
	echo Close this window, open a new cmd, and run the build again.
	echo.
	pause
	exit /b 1
)

echo.
echo MSVC is installed and ready:
echo.
cl

echo.
echo Now building the generator...
echo.

pushd "%~dp0..\.."
call scripts\windows\build_generator_msvc.bat
set "BUILD_EXIT=%errorlevel%"
popd

echo.
if "%BUILD_EXIT%"=="0" (
	echo Generator build completed.
) else (
	echo Generator build failed with exit code %BUILD_EXIT%.
)

echo.
pause
exit /b %BUILD_EXIT%