@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "VENV=.venv"
set "PYTHON=%VENV%\Scripts\python.exe"

echo.
echo === CapsWriter-Offline build ===

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

echo Installing or updating build dependencies...
"%PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto :failed
"%PYTHON%" -m pip install -r requirements-server.txt -r requirements-client.txt
if errorlevel 1 goto :failed

echo.
echo Building server and client...
"%PYTHON%" -m PyInstaller --noconfirm --clean build.spec
if errorlevel 1 goto :failed

echo.
echo Building client-only package...
"%PYTHON%" -m PyInstaller --noconfirm --clean build-client.spec
if errorlevel 1 goto :failed

echo.
echo Build complete. Executables are here:
echo   dist\CapsWriter-Offline\start_server.exe
echo   dist\CapsWriter-Offline\start_client.exe
echo   dist\CapsWriter-Offline-Client\start_client.exe
echo.
echo Note: the build uses directory junctions for models, assets, and source folders.
echo Keep the generated dist folder next to this source checkout, or package it with zip_release.py.
pause
exit /b 0

:failed
echo.
echo Build failed. See the error above.
pause
exit /b 1
