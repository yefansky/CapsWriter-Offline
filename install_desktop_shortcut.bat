@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo Creating the CapsWriter source desktop shortcut for the current Windows user...
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& ([scriptblock]::Create((Get-Content -LiteralPath '%~dp0scripts\install_desktop_shortcut.ps1' -Raw -Encoding UTF8))) -SourceRoot '%~dp0'"
if errorlevel 1 goto :failed

echo.
echo Desktop shortcut is ready.
exit /b 0

:failed
echo.
echo Shortcut creation failed. See the error above.
pause
exit /b 1
