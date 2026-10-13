@echo off
rem FRX - installer for Windows. Two modes:
rem   * next to FRX files (frx_setup.py)  - venv, requirements, first Telegram login, autostart (see frx_setup.py);
rem   * alone (just downloaded install.bat) - downloads FRX from GitHub into a folder, then does the same.
rem Env: FRX_DIR - install folder (default %USERPROFILE%\FRX), FRX_REPO - owner/repo, FRX_GITHUB_TOKEN - for a private repo.
chcp 65001 >nul
setlocal EnableExtensions
if not defined FRX_REPO set "FRX_REPO=Shxdowrate/FRX-Userbot"
cd /d "%~dp0"

call :find_python
if not defined PY call :install_python
if not defined PY (
  echo.
  echo Python 3.10+ not found. Install it from https://www.python.org/downloads/
  echo ^(check "Add python.exe to PATH"^) and run install.bat again.
  pause
  exit /b 1
)

if exist "%~dp0frx_setup.py" goto :setup

rem ------------------------------------------------------------------ download FRX from GitHub
set "FRX_DEST=%FRX_DIR%"
if not defined FRX_DEST set "FRX_DEST=%USERPROFILE%\FRX"
echo.
echo  FRX will be installed to:  %FRX_DEST%
set "ANS="
set /p "ANS=  Press Enter to continue, or type another folder: "
if defined ANS set "FRX_DEST=%ANS%"
echo.
echo  Downloading FRX from github.com/%FRX_REPO% ...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $h=@{}; if($env:FRX_GITHUB_TOKEN){ $h['Authorization']='token '+$env:FRX_GITHUB_TOKEN; $url='https://api.github.com/repos/'+$env:FRX_REPO+'/zipball' } else { $url='https://github.com/'+$env:FRX_REPO+'/archive/HEAD.zip' }; $tmp=Join-Path $env:TEMP ('frx_dl_'+[guid]::NewGuid().ToString('N')); New-Item -ItemType Directory -Path $tmp | Out-Null; try { $zip=Join-Path $tmp 'frx.zip'; Invoke-WebRequest -UseBasicParsing -UserAgent 'FRX-Installer' -Headers $h -Uri $url -OutFile $zip; Write-Host ('  downloaded: {0:N1} MB' -f ((Get-Item $zip).Length/1MB)); Expand-Archive -Path $zip -DestinationPath (Join-Path $tmp 'x') -Force; $src=Get-ChildItem (Join-Path $tmp 'x') -Directory | Select-Object -First 1; if(-not (Test-Path (Join-Path $src.FullName 'frx_setup.py'))){ throw 'archive has no frx_setup.py - wrong repository?' }; New-Item -ItemType Directory -Force -Path $env:FRX_DEST | Out-Null; Get-ChildItem -LiteralPath $src.FullName -Force | Where-Object { $_.Name -ne 'user' } | Copy-Item -Destination $env:FRX_DEST -Recurse -Force; Write-Host '  unpacked to' $env:FRX_DEST } finally { Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue }"
if errorlevel 1 (
  echo.
  echo  Download failed. Check the internet connection. If the repository is private, set FRX_GITHUB_TOKEN.
  pause
  exit /b 1
)
if not exist "%FRX_DEST%\frx_setup.py" (
  echo  Something went wrong: %FRX_DEST%\frx_setup.py not found.
  pause
  exit /b 1
)
cd /d "%FRX_DEST%"
echo.
echo  Files are ready. Starting setup...
%PY% -X utf8 frx_setup.py %*
echo.
echo  FRX folder: %FRX_DEST%   ^(next time run start.bat there^)
pause
exit /b 0

:setup
%PY% -X utf8 frx_setup.py %*
echo.
pause
exit /b 0

rem ------------------------------------------------------------------ helpers
:find_python
set "PY="
where py >nul 2>nul && py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=py -3"
if defined PY exit /b 0
where python >nul 2>nul && python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul && set "PY=python"
if defined PY exit /b 0
for %%V in (313 312 311 310) do (
  if not defined PY if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
)
exit /b 0

:install_python
where winget >nul 2>nul || exit /b 0
echo.
echo  Python 3.10+ is not installed. It can be installed now (Python 3.12 from Microsoft winget, about 30 MB).
set "ANS="
set /p "ANS=  Install Python now? [Y/n]: "
if /i "%ANS%"=="n" exit /b 0
if /i "%ANS%"=="no" exit /b 0
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
call :find_python
exit /b 0
