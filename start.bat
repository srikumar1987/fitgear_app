@echo off
setlocal
cd /d "%~dp0"
title TUR fitgear

rem The virtual environment lives outside OneDrive so it is not synced.
set "VENV=%LOCALAPPDATA%\TURfitgear\venv"
if not defined TUR_PORT set "TUR_PORT=5000"

if not exist "%VENV%\Scripts\python.exe" (
  echo First run: setting up TUR fitgear. This takes a minute...
  rem Use "python" if it is on PATH, otherwise the "py" launcher.
  set "PY=python"
  where python >nul 2>nul || set "PY=py -3"
  call %%PY%% -m venv "%VENV%" || goto :error
  rem Python 3.8 ships an old pip; upgrade it (pip picks the newest version that supports this Python).
  "%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -q --upgrade pip || goto :error
)

echo Checking packages...
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto :error

rem Run the app in the background (no console), so Ctrl+C or clicking a window cannot stop or freeze it.
start "" "%VENV%\Scripts\pythonw.exe" run.py

rem Wait up to 15 seconds for the app to answer.
"%VENV%\Scripts\python.exe" -c "import socket,sys,time;p=int(sys.argv[1]);ok=any(socket.socket().connect_ex(('127.0.0.1',p))==0 or time.sleep(0.5) for _ in range(30));sys.exit(0 if ok else 1)" %TUR_PORT%
if errorlevel 1 (
  echo.
  echo TUR fitgear did not start. See the log: %LOCALAPPDATA%\TURfitgear\data\tur_fitgear.log
  pause
  goto :eof
)
echo.
echo TUR fitgear is running at http://localhost:%TUR_PORT% and has opened in your browser.
echo This window will close by itself. To stop the app: Settings, then "Stop the app".
ping -n 5 127.0.0.1 >nul
goto :eof

:error
echo.
echo Setup failed. Check that Python 3.8 or newer is installed and that "python" (or "py") works in a Command Prompt.
echo The first run also needs an internet connection to download the required packages.
pause
