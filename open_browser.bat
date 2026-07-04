@echo off
REM Waits a few seconds for the server to come up, then opens the
REM default browser at the World Forge address.
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:5000"
