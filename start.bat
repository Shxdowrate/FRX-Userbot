@echo off
rem FRX - start on Windows (runs install.bat first if needed)
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  call install.bat --no-service
  if not exist ".venv\Scripts\python.exe" exit /b 1
)
".venv\Scripts\python.exe" -X utf8 -u frx_systemkeeper.py %*
pause
