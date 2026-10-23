@echo off
rem Run Magpie in Docker on Windows, as a server (most people want setup.cmd instead,
rem which installs the Magpie app). Double-click this file. It writes .env (no password:
rem Magpie opens on this computer only), starts Magpie and opens it in its own window,
rem and puts a Magpie icon on your Desktop and in the Start menu. Magpie then keeps itself
rem up to date. Run this again any time; your notes and settings are kept.
setlocal
cd /d "%~dp0"
set "MAGPIE_DIR=%~dp0"
title Magpie setup

where docker >nul 2>nul
if errorlevel 1 goto :nodocker
docker info >nul 2>nul
if errorlevel 1 goto :notrunning

echo Downloading Magpie. The first time takes a few minutes...
docker compose pull --quiet magpie
if not errorlevel 1 goto :pulled
echo Couldn't download Magpie, so building it on this computer instead. This takes a few minutes...
set "image="
for /f "delims=" %%i in ('docker compose config --images magpie') do if not defined image set "image=%%i"
if not defined image goto :failed
docker build --quiet -t "%image%" .
if errorlevel 1 goto :failed
:pulled
docker compose pull --quiet updates >nul 2>nul

docker compose run --rm --no-deps -v "%cd%:/setup" -w /setup magpie magpie setup --env-file /setup/.env
if errorlevel 1 goto :failed

docker compose up -d magpie
if errorlevel 1 goto :failed
docker compose up -d updates >nul 2>nul
if errorlevel 1 echo Automatic updates couldn't start, but Magpie works. To update by hand, run this setup again.
rem The image older versions built on this computer isn't needed any more.
docker image rm magpie:local >nul 2>nul

rem MAGPIE_HOST_PORT in .env moves Magpie off port 8000.
set "port=8000"
if exist .env for /f "usebackq eol=# tokens=1,* delims==" %%a in (".env") do if /i "%%a"=="MAGPIE_HOST_PORT" set "port=%%~b"
set "url=http://localhost:%port%"
echo Starting Magpie...
set tries=0
:wait
curl -fsS -o nul %url%/healthz >nul 2>nul
if not errorlevel 1 goto :ready
set /a tries+=1
if %tries% geq 60 goto :ready
ping -n 2 127.0.0.1 >nul
goto :wait

:ready
call :shortcuts
echo.
echo Magpie is ready. Open it any time from the Magpie icon on your Desktop or in the Start menu.
echo It updates itself and starts again by itself whenever Docker Desktop starts.
echo To stop it, open a terminal in this folder and run: docker compose stop
call "%MAGPIE_DIR%magpie.cmd"
echo.
pause
exit /b 0

:shortcuts
rem The Magpie icons run magpie.cmd minimized, which opens Magpie in its own
rem window. Running the setup again replaces them rather than adding more.
rem Unblock-File stops Windows asking about magpie.cmd each time, as it came in a download.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; $dir = $env:MAGPIE_DIR; Unblock-File -LiteralPath (Join-Path $dir 'magpie.cmd') -ErrorAction SilentlyContinue; $shell = New-Object -ComObject WScript.Shell; foreach ($place in 'Desktop', 'Programs') { $link = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath($place)) 'Magpie.lnk')); $link.TargetPath = Join-Path $dir 'magpie.cmd'; $link.WorkingDirectory = $dir; $link.WindowStyle = 7; $link.IconLocation = (Join-Path $dir 'magpie.ico') + ',0'; $link.Description = 'Open Magpie'; $link.Save() }" >nul 2>nul
if errorlevel 1 echo Couldn't add the Magpie icons, but you can open Magpie with magpie.cmd in this folder.
echo Magpie also opens when you sign in to Windows (to stop that, delete Magpie from the
echo Startup folder: press Windows+R and type shell:startup).
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference = 'Stop'; $dir = $env:MAGPIE_DIR; $link = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) 'Magpie.lnk')); $link.TargetPath = Join-Path $dir 'magpie.cmd'; $link.WorkingDirectory = $dir; $link.WindowStyle = 7; $link.IconLocation = (Join-Path $dir 'magpie.ico') + ',0'; $link.Description = 'Open Magpie'; $link.Save()" >nul 2>nul
if errorlevel 1 echo Couldn't set Magpie to open when you sign in.
exit /b 0

:nodocker
echo Magpie runs in Docker, which isn't installed yet.
echo Install Docker Desktop from https://www.docker.com/products/docker-desktop/
echo then start it and double-click this file again.
pause
exit /b 1

:notrunning
echo Docker Desktop is installed but not running.
echo Start Docker Desktop, wait until it says Engine running, then double-click this file again.
pause
exit /b 1

:failed
echo.
echo Something went wrong; the messages above say what. Fix it and double-click this file again.
pause
exit /b 1
