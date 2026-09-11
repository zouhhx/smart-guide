@echo off
setlocal
cd /d "%~dp0"
set /p DEVICE=Input device index (press Enter for Windows default): 
echo Voice gateway is starting. Press Ctrl+C to stop.
if "%DEVICE%"=="" (
  "VoiceGateway-G10C-Test.exe" --config "config\default.json" mic
) else (
  "VoiceGateway-G10C-Test.exe" --config "config\default.json" mic --device "%DEVICE%"
)
set RESULT=%ERRORLEVEL%
echo.
echo Exit code: %RESULT%
pause
exit /b %RESULT%
