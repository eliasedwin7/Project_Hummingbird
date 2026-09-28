@echo off
rem One-time setup: a separate Python environment for the AI pilot, so the AI
rem libraries (PyTorch etc.) never interfere with your main Python.
rem It lives outside Google Drive (%USERPROFILE%\.venvs\hummingbird) to avoid syncing ~1 GB of libraries.
title Tello AI setup
cd /d "%~dp0"
set "VENV=%USERPROFILE%\.venvs\hummingbird"

set "PY=python"
where python >nul 2>nul || set "PY=%USERPROFILE%\anaconda3\python.exe"

if not exist "%VENV%\Scripts\python.exe" (
  echo Creating AI environment in %VENV% ...
  "%PY%" -m venv "%VENV%" || goto :fail
)
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip
"%VENV%\Scripts\python.exe" -m pip install torch --index-url https://download.pytorch.org/whl/cpu || goto :fail
"%VENV%\Scripts\python.exe" -m pip install gymnasium stable-baselines3 aiohttp || goto :fail
echo.
echo AI environment ready. Run train.bat to train the pilot.
pause
exit /b 0

:fail
echo.
echo Setup failed - see the messages above.
pause
exit /b 1
