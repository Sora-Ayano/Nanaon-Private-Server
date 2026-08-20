@echo off
setlocal
"%~dp0runtime\python\python.exe" -B "%~dp0run.py" --stop
exit /b %ERRORLEVEL%
