@echo off
title Tello Sim
cd /d "%~dp0"

rem Prefer the AI environment (from setup_ai.bat): it can also run the AI pilot (press I in the game).
set "PY=%USERPROFILE%\.venvs\hummingbird\Scripts\python.exe"
if exist "%PY%" goto :run

set "PY=python"
where python >nul 2>nul || set "PY=%USERPROFILE%\anaconda3\python.exe"
"%PY%" -c "import aiohttp" 2>nul || (
  echo Installing required Python packages...
  "%PY%" -m pip install -r requirements.txt
)

:run
"%PY%" game_server.py %*
pause
