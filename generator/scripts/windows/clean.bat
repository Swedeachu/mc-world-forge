@echo off
setlocal
set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..\..") do set "GENERATOR_ROOT=%%~fI"
if exist "%GENERATOR_ROOT%\build" rmdir /s /q "%GENERATOR_ROOT%\build"
if exist "%GENERATOR_ROOT%\bin\mc_world_forge_generator.exe" del /q "%GENERATOR_ROOT%\bin\mc_world_forge_generator.exe"
if exist "%GENERATOR_ROOT%\bin\mc_world_forge_generator.pdb" del /q "%GENERATOR_ROOT%\bin\mc_world_forge_generator.pdb"
echo Native generator build outputs removed.
