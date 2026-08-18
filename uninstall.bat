@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo Removing CapsWriter Offline startup integration...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" start_manager.py --stop --disable-startup
) else (
    reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "CapsWriterOfflineManager" /f >nul 2>nul
)

echo.
echo CapsWriter has been stopped and removed from Windows startup.
echo Your models, logs, and hotword files were kept.
exit /b 0
