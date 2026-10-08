@echo off
rem FRX: fold the project before publishing - removes personal data (user/, sessions, caches). See frx_pack.py
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -X utf8 frx_pack.py %*
) else (
  python -X utf8 frx_pack.py %*
)
echo.
pause
