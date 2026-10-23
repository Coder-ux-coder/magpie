@echo off
rem Install Magpie on Windows: double-click this file.
rem It downloads the Magpie app (MagpieSetup.exe) and installs it, with nothing to answer.
rem Magpie then starts with Windows, listens for "notes on" and keeps itself up to date.
rem If you used Magpie in Docker before, your notes and settings come over by themselves.
rem (To run Magpie in Docker as a server instead, use setup-docker.cmd.)
setlocal
title Magpie setup
set "installer=%TEMP%\MagpieSetup.exe"
echo Downloading Magpie...
curl -fL --retry 3 --progress-bar -o "%installer%" https://github.com/Coder-ux-coder/magpie/releases/latest/download/MagpieSetup.exe
if errorlevel 1 goto :failed
echo Installing Magpie...
"%installer%" /SILENT /SUPPRESSMSGBOXES /NORESTART /SP-
if errorlevel 1 goto :failed
del "%installer%" >nul 2>nul
echo.
echo Magpie is installed and running: its icon is by the clock.
echo Say "notes on" any time to take a note, and "notes off" to save it.
timeout /t 10 >nul
exit /b 0

:failed
echo.
echo Magpie couldn't be downloaded or installed. Download MagpieSetup.exe from
echo https://github.com/Coder-ux-coder/magpie/releases/latest and double-click it.
pause
exit /b 1
