@echo off
setlocal
cd /d "%~dp0"
set /p DEVICE=Input device index (press Enter for Windows default): 
echo Speak continuously at normal volume for 8 seconds.
if "%DEVICE%"=="" (
  "VoiceGateway-G10C-Test.exe" --config "config\default.json" probe --seconds 8
) else (
  "VoiceGateway-G10C-Test.exe" --config "config\default.json" probe --device "%DEVICE%" --seconds 8
)
set RESULT=%ERRORLEVEL%
echo.
echo Exit code: %RESULT%
pause
exit /b %RESULT%
