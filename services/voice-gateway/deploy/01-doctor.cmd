@echo off
setlocal
cd /d "%~dp0"
"VoiceGateway-G10C-Test.exe" --config "config\default.json" doctor
set RESULT=%ERRORLEVEL%
echo.
echo Exit code: %RESULT%
pause
exit /b %RESULT%
