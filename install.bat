@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo Installing CapsWriter Offline for the current Windows user...
call "%~dp0run.bat"
if errorlevel 1 goto :failed

".venv\Scripts\python.exe" start_manager.py --enable-startup
if errorlevel 1 goto :failed

echo.
echo Installed successfully.
echo CapsWriter will start automatically after you sign in to Windows.
exit /b 0

:failed
echo.
echo Installation failed. See the error above.
pause
exit /b 1
