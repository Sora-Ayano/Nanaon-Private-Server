@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-And-Run.ps1" -ResourcesOnly %*
set "NANAON_EXIT=%ERRORLEVEL%"
echo.
if not "%NANAON_EXIT%"=="0" echo Resource push failed with exit code %NANAON_EXIT%. See the message above.
pause
exit /b %NANAON_EXIT%
