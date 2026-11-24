@echo off
setlocal
cd /d "%~dp0"
title TUR fitgear
if not defined TUR_PORT set "TUR_PORT=5000"
set "VENV=%LOCALAPPDATA%\TURfitgear\venv"
if not exist "%VENV%\Scripts\python.exe" (
  echo TUR fitgear is not set up on this PC yet.
  pause
  goto :eof
)
"%VENV%\Scripts\python.exe" fitgear_start.py --stop
if errorlevel 1 (
  pause
  goto :eof
)
echo TUR fitgear is stopped. Double-click start.bat to start it again.
ping -n 4 127.0.0.1 >nul
