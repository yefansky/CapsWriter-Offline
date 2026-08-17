@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "VENV=.venv"
set "PYTHON=%VENV%\Scripts\python.exe"
set "PYTHONW=%VENV%\Scripts\pythonw.exe"

echo.
echo === CapsWriter-Offline source runner ===

if not exist "%PYTHON%" (
    echo Creating Python 3.11 virtual environment...
    py -3.11 -m venv "%VENV%"
    if errorlevel 1 (
        echo.
        echo ERROR: Python 3.11 was not found. Install Python 3.11 x64, then retry.
        pause
        exit /b 1
    )

)

call "%~dp0setup_llama_runtime.bat"
if errorlevel 1 goto :failed

echo Checking dependencies...
"%PYTHON%" -m pip install -r requirements-server.txt -r requirements-client.txt
if errorlevel 1 goto :failed

echo Starting the unified local input manager...
start "" /b "%PYTHONW%" start_manager.py --restart

echo.
echo Started. The manager is the only taskbar entry and owns the local engine.
echo If recognition fails, download and unpack an ASR model under models\ first.
exit /b 0

:failed
echo.
echo Setup failed. See the error above.
pause
exit /b 1
