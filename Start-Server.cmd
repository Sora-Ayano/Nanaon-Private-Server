@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
if exist "%~dp0.venv\Scripts\python.exe" (
  set "nanaon_python=%~dp0.venv\Scripts\python.exe"
  goto run_server
)
if not exist "%~dp0runtime\python\python.exe" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Setup-Runtime.ps1"
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
set "nanaon_python=%~dp0runtime\python\python.exe"
:run_server
"%nanaon_python%" -B "%~dp0lan_launcher.py" %*
set "nanaon_result=%ERRORLEVEL%"
if not "%nanaon_result%"=="0" pause
exit /b %nanaon_result%
