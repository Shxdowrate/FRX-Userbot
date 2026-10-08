@echo off
rem FRX - install on Windows: venv, requirements, first Telegram login, autostart. See frx_setup.py
chcp 65001 >nul
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -3"
if not defined PY (
  where python >nul 2>nul && python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo Python 3.10+ not found. Install it from https://www.python.org/downloads/ ^(check "Add python.exe to PATH"^) and run install.bat again.
  pause
  exit /b 1
)
%PY% -X utf8 frx_setup.py %*
echo.
pause
