@echo off
rem Open Magpie in its own window. The setup puts a Magpie icon on your Desktop
rem and in the Start menu that runs this, so you never need to type an address.
rem It starts Docker Desktop and Magpie if they aren't running, waits until
rem Magpie answers, then opens it in an app window (Edge or Chrome).
setlocal
cd /d "%~dp0"
title Magpie
rem MAGPIE_HOST_PORT in .env moves Magpie off port 8000.
set "port=8000"
if exist .env for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do if /i "%%a"=="MAGPIE_HOST_PORT" set "port=%%~b"
set "url=http://localhost:%port%"

curl -fsS -o nul --max-time 3 %url%/healthz >nul 2>nul
if not errorlevel 1 goto :open

where docker >nul 2>nul
if errorlevel 1 goto :nodocker
docker info >nul 2>nul
if not errorlevel 1 goto :dockerok
if exist "%ProgramFiles%\Docker\Docker\Docker Desktop.exe" start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
echo Starting Docker Desktop...
set tries=0
:waitdocker
docker info >nul 2>nul
if not errorlevel 1 goto :dockerok
set /a tries+=1
if %tries% geq 120 goto :notrunning
ping -n 2 127.0.0.1 >nul
goto :waitdocker

:dockerok
rem Start Magpie if it was stopped (or create it again if it was removed); this
rem never changes your notes or settings.
docker compose start >nul 2>nul
if errorlevel 1 docker compose up -d >nul 2>nul
echo Starting Magpie...
set tries=0
:waitmagpie
curl -fsS -o nul --max-time 3 %url%/healthz >nul 2>nul
if not errorlevel 1 goto :open
set /a tries+=1
if %tries% geq 120 goto :notstarted
ping -n 2 127.0.0.1 >nul
goto :waitmagpie

:open
rem An app window: no address bar or tabs, like any other program.
set "browser="
for /f "tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe" /ve 2^>nul ^| "%SystemRoot%\System32\find.exe" "REG_SZ"') do call :try "%%b"
for /f "tokens=2,*" %%a in ('reg query "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\msedge.exe" /ve 2^>nul ^| "%SystemRoot%\System32\find.exe" "REG_SZ"') do call :try "%%b"
call :try "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
call :try "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
call :try "%LocalAppData%\Microsoft\Edge\Application\msedge.exe"
for /f "tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" /ve 2^>nul ^| "%SystemRoot%\System32\find.exe" "REG_SZ"') do call :try "%%b"
for /f "tokens=2,*" %%a in ('reg query "HKCU\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe" /ve 2^>nul ^| "%SystemRoot%\System32\find.exe" "REG_SZ"') do call :try "%%b"
call :try "%ProgramFiles%\Google\Chrome\Application\chrome.exe"
call :try "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
call :try "%LocalAppData%\Google\Chrome\Application\chrome.exe"
if not defined browser goto :plain
start "" "%browser%" --app=%url%
exit /b 0
:plain
start "" %url%
exit /b 0

:try
if defined browser exit /b 0
if exist "%~1" set "browser=%~1"
exit /b 0

:nodocker
call :tell "Magpie runs in Docker Desktop, which is not installed. Install it from docker.com, then run setup.cmd in the Magpie folder."
exit /b 1

:notrunning
call :tell "Docker Desktop is not running. Start Docker Desktop, wait until it says Engine running, then open Magpie again."
exit /b 1

:notstarted
call :tell "Magpie did not start. To see why, open a terminal in the Magpie folder and run: docker compose logs magpie"
exit /b 1

:tell
echo %~1
rem The Magpie icon runs this window minimized, so show the message in a box too.
powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [void][System.Windows.MessageBox]::Show('%~1', 'Magpie')" >nul 2>nul
exit /b 0
