@echo off
title Tello Pilot
cd /d "%~dp0"

set "PY=python"
where python >nul 2>nul || set "PY=%USERPROFILE%\anaconda3\python.exe"

"%PY%" -c "import aiohttp, av, PIL" 2>nul || (
  echo Installing required Python packages...
  "%PY%" -m pip install -r requirements.txt
)

"%PY%" tello_bridge.py %*
pause
