@echo off
rem Set up and start Magpie with Docker on Windows. Double-click this file.
rem It asks for your Claude API key, a password and your time zone, writes them
rem to .env, starts Magpie and opens it in your browser. Run it again any time
rem to change those answers; your notes are kept.
setlocal
cd /d "%~dp0"
title Magpie setup

where docker >nul 2>nul
if errorlevel 1 goto :nodocker
docker info >nul 2>nul
if errorlevel 1 goto :notrunning

echo Building Magpie. The first time takes a few minutes...
docker compose build --quiet magpie
if errorlevel 1 goto :failed

docker compose run --rm --no-deps -v "%cd%:/setup" -w /setup magpie magpie setup --env-file /setup/.env
if errorlevel 1 goto :failed

docker compose up -d
if errorlevel 1 goto :failed

echo Starting Magpie...
set tries=0
:wait
curl -fsS -o nul http://localhost:8000/healthz >nul 2>nul
if not errorlevel 1 goto :ready
set /a tries+=1
if %tries% geq 60 goto :ready
timeout /t 1 /nobreak >nul
goto :wait

:ready
echo.
echo Magpie is running at http://localhost:8000
echo It starts again by itself whenever Docker Desktop starts.
echo To stop it, open a terminal in this folder and run: docker compose stop
start "" http://localhost:8000
echo.
pause
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
