@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "VENV=%CD%\dependencies\ecapa\.venv\Scripts"
set "PYTHON=%VENV%\python.exe"

if not exist "%PYTHON%" (
    echo [ERROR] Python venv not found: %VENV%
    echo Please run run.bat first.
    pause
    exit /b 1
)

set "PATH=%VENV%;%PATH%"
set "VIRTUAL_ENV=%CD%\dependencies\ecapa\.venv"
set "PYTHONIOENCODING=utf-8"

"%PYTHON%" "0a-UVR5\menu.py"
pause
