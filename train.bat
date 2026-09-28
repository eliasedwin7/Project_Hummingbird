@echo off
rem Train (or keep training) the AI pilot. Extra options are passed through, e.g.:
rem   train.bat --steps 2000000 --resume
title Tello AI pilot training
cd /d "%~dp0"
set "VENV=%USERPROFILE%\.venvs\hummingbird"
if not exist "%VENV%\Scripts\python.exe" (
  echo The AI environment isn't set up yet. Run setup_ai.bat first.
  pause
  exit /b 1
)
"%VENV%\Scripts\python.exe" train_pilot.py %*
pause
