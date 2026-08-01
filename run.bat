@echo off
cd /d "%~dp0"

set "VENV=%CD%\dependencies\ecapa\.venv\Scripts"
set "PYTHON=%VENV%\python.exe"

if not exist "%PYTHON%" (
    echo [ERROR] ECAPA venv not found: %VENV%
    echo Please run: cd dependencies\ecapa ^&^& powershell -File setup.ps1
    pause
    exit /b 1
)

echo Activating ECAPA environment...
set "PATH=%VENV%;%PATH%"
set "VIRTUAL_ENV=%CD%\dependencies\ecapa\.venv"

echo Checking Speaker Engine...
"%PYTHON%" -c "import torch; print('  CUDA:', torch.cuda.is_available()); import speechbrain; print('  SpeechBrain OK')" 2>nul
if errorlevel 1 (
    echo [WARN] Speaker Engine not available
)

echo.
echo Installing core dependencies...
"%PYTHON%" -m ensurepip --default-pip 2>nul
"%PYTHON%" -m pip install flask flask-cors pysubs2 pyyaml numpy -q 2>nul
if errorlevel 1 (
    echo [ERROR] Dependency install failed
    pause
    exit /b 1
)

echo   Ready.
echo.
echo Starting Voice STCut V0.3 at http://localhost:8766...
start http://localhost:8766
"%PYTHON%" -m backend.app
pause
