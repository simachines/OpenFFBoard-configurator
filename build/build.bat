@echo off
setlocal

if exist "%~dp0..\setup_venv_win.bat" (
	call "%~dp0..\setup_venv_win.bat"
)

if exist "%~dp0..\.venv\Scripts\python.exe" (
	"%~dp0..\.venv\Scripts\python.exe" -m PyInstaller --noconfirm OpenFFBoard.spec
) else (
	py -3.12 -m PyInstaller --noconfirm OpenFFBoard.spec
)

if exist build rmdir /s /q build

if %ERRORLEVEL% NEQ 0 (
	pause
)

