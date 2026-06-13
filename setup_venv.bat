@echo off
setlocal

set "ROOT=%~dp0"

if not exist "%ROOT%.venv\Scripts\python.exe" (
  py -3.12 -m venv "%ROOT%.venv"
)

"%ROOT%.venv\Scripts\python.exe" -m ensurepip --upgrade
"%ROOT%.venv\Scripts\python.exe" -m pip install -r "%ROOT%requirements.txt" pyinstaller

if %ERRORLEVEL% NEQ 0 (
  pause
)