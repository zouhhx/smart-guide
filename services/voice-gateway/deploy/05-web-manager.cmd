@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
if not exist "VoiceGateway-WebUI.exe" (
  echo VoiceGateway-WebUI.exe is missing.
  echo Run webui\build-web-manager.ps1 on the development machine first.
  pause
  exit /b 2
)
echo Starting the Voice Gateway management page...
echo Local URL: http://127.0.0.1:8765/
echo LAN URL:   http://%COMPUTERNAME%:8765/
echo If another computer cannot connect, run 06-enable-lan-access.cmd once as administrator.

set "FIRST_START=1"
set /a RESTART_FAILURES=0

:run_web_manager
if "!FIRST_START!"=="1" (
  set "FIRST_START=0"
  "VoiceGateway-WebUI.exe" --host 0.0.0.0 --port 8765 --open-browser
) else (
  "VoiceGateway-WebUI.exe" --host 0.0.0.0 --port 8765
)
set "RESULT=!ERRORLEVEL!"

if "!RESULT!"=="0" (
  echo.
  echo Voice Gateway management was stopped normally.
  exit /b 0
)

if "!RESULT!"=="3" (
  echo.
  echo Voice Gateway management cannot bind to TCP port 8765.
  echo Check whether another Web manager is already running, then start this launcher again.
  exit /b 3
)

set /a RESTART_FAILURES+=1
set "RESTART_DELAY=60"
if !RESTART_FAILURES! EQU 1 set "RESTART_DELAY=2"
if !RESTART_FAILURES! EQU 2 set "RESTART_DELAY=5"
if !RESTART_FAILURES! EQU 3 set "RESTART_DELAY=10"
if !RESTART_FAILURES! EQU 4 set "RESTART_DELAY=30"

echo.
echo Voice Gateway management exited unexpectedly with code !RESULT!.
echo Restarting in !RESTART_DELAY! seconds. Close this window to stop recovery.
timeout /t !RESTART_DELAY! /nobreak >nul 2>&1
if errorlevel 1 (
  powershell.exe -NoProfile -NonInteractive -Command "Start-Sleep -Seconds !RESTART_DELAY!"
)
goto run_web_manager
